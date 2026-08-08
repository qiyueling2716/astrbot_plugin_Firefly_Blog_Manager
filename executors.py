"""模块说明见 main.py 顶部文档。"""

from __future__ import annotations

import asyncio
import os
import re
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Optional

from astrbot.api import logger, AstrBotConfig

from .constants import DEFAULT_SSH_PORT, SSH_KEEPALIVE_INTERVAL, SSH_KEEPALIVE_COUNT_MAX
from .models import ConfigurationError, SSHConnectionError



# ============================================================================
# 命令执行器抽象
# ============================================================================

def _sanitize_command(command: str) -> str:
    """移除命令中的敏感信息（密码等），用于日志输出"""
    return re.sub(r"export SSHPASS='[^']*'", "export SSHPASS='***'", command)


def _validate_shell_command(command: str) -> bool:
    """检查命令是否包含危险的 shell 注入模式

    作为纵深防御手段，在命令执行前检测 $(...) 和反引号等命令替换语法。
    所有用户可控参数应已在调用前通过 shlex.quote() 转义，此检查仅作
    最后一道防线。
    """
    dangerous = [
        (r'\$\(', '$(...) 命令替换'),
        (r'`[^`]+`', '反引号命令替换'),
    ]
    for pattern, desc in dangerous:
        if re.search(pattern, command):
            logger.warning(f"[Security] 命令包含潜在危险的 shell 模式 ({desc})，已拒绝执行")
            logger.debug(f"[Security] 被拒绝的命令: {_sanitize_command(command)}")
            return False
    return True


class CommandExecutor(ABC):
    """命令执行器抽象基类，统一本地和远程命令执行接口"""

    @abstractmethod
    async def run(self, command: str, cwd: Optional[str] = None, timeout: int = 300) -> tuple[int, str, str]:
        """执行命令，返回 (returncode, stdout, stderr)"""
        pass

    @abstractmethod
    async def close(self):
        """关闭连接/清理资源"""
        pass


class LocalExecutor(CommandExecutor):
    """本地命令执行器，使用 asyncio 子进程"""

    async def run(self, command: str, cwd: Optional[str] = None, timeout: int = 300) -> tuple[int, str, str]:
        """执行命令，返回 (returncode, stdout, stderr)

        安全说明：使用 create_subprocess_shell 是为了支持包含 shell 特性
        （管道、重定向、&& 链式命令）的构建命令。所有用户可控的参数（路径、
        主机名等）在调用前已通过 shlex.quote() 进行转义。执行前会进行
        命令注入模式检测作为最后一道防线。
        """
        logger.debug(f"[LocalExecutor] 执行命令: {_sanitize_command(command)}")

        # 安全最佳实践：执行前检查命令是否包含危险的 shell 注入模式
        if not _validate_shell_command(command):
            return -1, "", "命令包含潜在危险的 shell 模式，已被拒绝执行"
        
        try:
            # 验证工作目录
            if cwd and not os.path.isdir(cwd):
                return -1, "", f"工作目录不存在: {cwd}"

            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy(),
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                returncode = proc.returncode or 0
                
                if returncode != 0:
                    logger.debug(f"[LocalExecutor] 命令执行失败: {_sanitize_command(command)}, 返回码: {returncode}, 错误: {stderr.decode('utf-8', errors='replace')}")
                
                return (
                    returncode,
                    stdout.decode("utf-8", errors="replace"),
                    stderr.decode("utf-8", errors="replace"),
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                logger.error(f"[LocalExecutor] 命令执行超时: {_sanitize_command(command)}")
                return -1, "", f"命令执行超时（{timeout}秒）"
        except PermissionError:
            logger.error(f"[LocalExecutor] 权限不足: {_sanitize_command(command)}")
            return -1, "", "权限不足，请检查文件或目录权限"
        except FileNotFoundError:
            logger.error(f"[LocalExecutor] 命令未找到: {_sanitize_command(command)}")
            return -1, "", "命令未找到，请检查是否安装了相关工具"
        except Exception as e:
            logger.error(f"[LocalExecutor] 执行命令异常: {_sanitize_command(command)}, 错误: {e}")
            return -1, "", "命令执行异常，请检查日志获取详细信息"

    async def close(self):
        """关闭执行器（本地执行器无需特殊清理）"""
        pass


class ConnectionStatus(Enum):
    """SSH 连接状态枚举"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"

class RemoteExecutor(CommandExecutor):
    """远程 SSH 命令执行器，使用 asyncssh 异步连接，按需建立，带保活和重连"""

    def __init__(self, config: AstrBotConfig):
        self.config = config
        self._conn = None
        self._sftp = None
        self._lock = asyncio.Lock()
        self._connect_attempts = 0
        self._max_connect_attempts = 3
        self._status = ConnectionStatus.DISCONNECTED
        self._last_error = None
        # 性能优化：记录最近一次连接验证时间，避免每条命令前都发送探测
        self._last_probe_time = 0.0

    @property
    def status(self) -> ConnectionStatus:
        """获取当前连接状态"""
        return self._status

    def _mark_activity(self):
        """标记连接活动，更新最近验证时间"""
        self._last_probe_time = time.monotonic()

    async def _ensure_connected(self, retry: int = 0):
        """确保 SSH 连接已建立（带锁防止并发连接竞争，支持重试）"""
        async with self._lock:
            # 如果正在连接，等待完成
            if self._status == ConnectionStatus.CONNECTING:
                await asyncio.sleep(0.5)
                return await self._ensure_connected(retry)

            # 如果已有连接，按需检查存活：距上次成功交互超过
            # SSH_KEEPALIVE_INTERVAL 才发送探测，避免每次命令前
            # 都产生一次额外的 SSH 往返延迟
            if self._conn is not None and self._status == ConnectionStatus.CONNECTED:
                if time.monotonic() - self._last_probe_time < SSH_KEEPALIVE_INTERVAL:
                    return
                try:
                    # 发送 keepalive 探测
                    await self._conn.run("echo ok", timeout=5)
                    self._mark_activity()
                    return
                except Exception as e:
                    logger.warning(f"[SSH] 连接已断开，尝试重连: {e}")
                    self._conn = None
                    self._sftp = None
                    self._status = ConnectionStatus.DISCONNECTED

            try:
                import asyncssh
            except ImportError:
                self._status = ConnectionStatus.ERROR
                raise RuntimeError("远程模式需要 asyncssh 库，请安装: pip install asyncssh")

            hostname = self.config.get("server_ip", "")
            port = self.config.get("server_port", DEFAULT_SSH_PORT)
            username = self.config.get("username", "")
            auth_type = self.config.get("auth_type", "key")

            if not hostname:
                self._status = ConnectionStatus.ERROR
                raise ConfigurationError("缺少 server_ip 配置")
            if not username:
                self._status = ConnectionStatus.ERROR
                raise ConfigurationError("缺少 username 配置")

            connect_options: dict = {
                "host": hostname,
                "port": port,
                "username": username,
                "keepalive_interval": SSH_KEEPALIVE_INTERVAL,
                "keepalive_count_max": SSH_KEEPALIVE_COUNT_MAX,
                "connect_timeout": 15,
            }
            # 安全最佳实践：使用 known_hosts 进行主机密钥验证，防止中间人攻击
            # 如果配置了已知主机文件路径则使用，否则使用 asyncssh 默认的 ~/.ssh/known_hosts
            known_hosts_path = self.config.get("ssh_known_hosts_path", "").strip()
            if known_hosts_path:
                expanded = os.path.expanduser(known_hosts_path)
                connect_options["known_hosts"] = expanded
                logger.debug(f"[SSH] 使用 known_hosts: {expanded}")

            if auth_type == "password":
                password = self.config.get("password", "")
                if not password:
                    self._status = ConnectionStatus.ERROR
                    raise ConfigurationError("密码认证方式但未配置 password")
                connect_options["password"] = password
                logger.info("[SSH] 使用密码认证连接远程服务器")
            else:
                key_path = self.config.get("private_key_path", "")
                if key_path and os.path.exists(key_path):
                    connect_options["client_keys"] = [key_path]
                    logger.info("[SSH] 使用密钥认证连接远程服务器")
                else:
                    logger.warning(f"[SSH] 私钥文件不存在: {key_path}，尝试使用 SSH Agent")

            self._status = ConnectionStatus.CONNECTING
            try:
                self._conn = await asyncssh.connect(**connect_options)
                self._connect_attempts = 0
                self._status = ConnectionStatus.CONNECTED
                self._last_error = None
                self._mark_activity()
                logger.info("[SSH] 连接成功")
            except asyncssh.Error as e:
                self._connect_attempts += 1
                self._last_error = str(e)
                logger.error(f"[SSH] 连接失败 (第 {self._connect_attempts} 次): {e}")
                
                if self._connect_attempts < self._max_connect_attempts:
                    wait_time = 2 ** self._connect_attempts  # 指数退避
                    logger.info(f"[SSH] {wait_time} 秒后重试...")
                    await asyncio.sleep(wait_time)
                    await self._ensure_connected(retry + 1)
                else:
                    self._status = ConnectionStatus.ERROR
                    raise SSHConnectionError(f"SSH 连接失败，已重试 {self._max_connect_attempts} 次: {e}")

    async def run(self, command: str, cwd: Optional[str] = None, timeout: int = 300) -> tuple[int, str, str]:
        """执行远程命令，返回 (returncode, stdout, stderr)"""
        logger.debug(f"[RemoteExecutor] 执行命令: {_sanitize_command(command)}")
        
        try:
            await self._ensure_connected()

            if cwd:
                command = f"cd {cwd} && {command}"

            try:
                result = await self._conn.run(command, timeout=timeout)
                returncode = result.exit_status or 0
                self._mark_activity()
                
                if returncode != 0:
                    logger.debug(f"[RemoteExecutor] 命令执行失败: {_sanitize_command(command)}, 返回码: {returncode}")
                
                return returncode, result.stdout or "", result.stderr or ""
            except asyncssh.TimeoutError:
                logger.error(f"[RemoteExecutor] 命令执行超时: {_sanitize_command(command)}")
                # 超时不重置连接，可能是命令本身耗时太长
                return -1, "", f"命令执行超时（{timeout}秒）"
            except asyncssh.Error as e:
                logger.error(f"[RemoteExecutor] SSH 错误: {_sanitize_command(command)}, 错误: {e}")
                # 重置连接状态，下次自动重连
                self._conn = None
                self._sftp = None
                self._status = ConnectionStatus.DISCONNECTED
                self._last_error = str(e)
                return -1, "", "SSH 命令执行失败，请检查日志获取详细信息"
        except SSHConnectionError as e:
            return -1, "", "SSH 连接失败，请检查日志获取详细信息"
        except ConfigurationError as e:
            return -1, "", "SSH 配置错误，请检查日志获取详细信息"

    async def get_sftp(self):
        """获取 SFTP 客户端（复用已有连接）"""
        await self._ensure_connected()
        if self._sftp is None:
            try:
                self._sftp = await self._conn.start_sftp_client()
            except Exception as e:
                logger.error(f"[SSH] 创建 SFTP 客户端失败: {e}")
                raise
        return self._sftp

    async def close(self):
        """关闭 SSH 连接并清理资源"""
        async with self._lock:
            if self._sftp:
                try:
                    self._sftp.exit()
                except Exception as e:
                    logger.warning(f"[SSH] 关闭 SFTP 客户端失败: {e}")
                self._sftp = None
            
            if self._conn:
                try:
                    self._conn.close()
                except Exception as e:
                    logger.warning(f"[SSH] 关闭连接失败: {e}")
                self._conn = None
            
            self._status = ConnectionStatus.DISCONNECTED
            self._last_error = None
            logger.info("[SSH] 连接已关闭")

    def reset_connection(self):
        """主动重置连接状态（用于手动重连）"""
        self._conn = None
        self._sftp = None
        self._status = ConnectionStatus.DISCONNECTED
        self._connect_attempts = 0
        self._last_error = None
        logger.info("[SSH] 连接状态已重置")

