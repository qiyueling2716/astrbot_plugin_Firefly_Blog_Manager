"""模块说明见 main.py 顶部文档。"""

from __future__ import annotations

import asyncio
import os
import posixpath
import shlex
import shutil
import tempfile
from typing import Optional

from astrbot.api import logger, AstrBotConfig

from .common import is_firefly_blog_local
from .constants import BUILD_TIMEOUT, DEPLOY_TIMEOUT
from .executors import CommandExecutor, RemoteExecutor
from .models import DeployMode



# ============================================================================
# 构建部署管理器
# ============================================================================

class BuildDeployManager:
    """构建和部署管理器，负责博客的构建和部署流程"""

    def __init__(self, config: AstrBotConfig, local_executor: CommandExecutor, remote_executor: Optional[RemoteExecutor] = None):
        self.config = config
        self.local_executor = local_executor
        self.remote_executor = remote_executor
        
        # 校验部署模式配置
        deploy_mode_value = config.get("deploy_mode", "local_build")
        if deploy_mode_value not in [m.value for m in DeployMode]:
            logger.warning(f"[BuildDeployManager] 无效的部署模式: {deploy_mode_value}，使用默认值 local_build")
            deploy_mode_value = "local_build"
        self.deploy_mode = DeployMode(deploy_mode_value)
        
        # 校验路径配置
        self.blog_root = self._validate_path(config.get("local_blog_root", "/var/www/firefly"), "local_blog_root")
        self.remote_blog_root = self._validate_path(config.get("remote_blog_root", "/var/www/firefly"), "remote_blog_root")
        self.web_root = self._validate_path(config.get("web_root", "/var/www/html"), "web_root")
        self.remote_web_root = self._validate_path(config.get("remote_web_root", "/var/www/html"), "remote_web_root")
        
        # 构建并发锁：allow_build_concurrent 为 false 时串行化构建，避免内存竞争
        self._build_lock = asyncio.Lock()
        
        logger.info(f"[BuildDeployManager] 初始化完成 - 部署模式: {self.deploy_mode.value}")

    def _validate_path(self, path: str, config_name: str) -> str:
        """校验路径配置的有效性，防止路径遍历"""
        if not isinstance(path, str) or not path.strip():
            logger.warning(f"[BuildDeployManager] {config_name} 配置无效，使用默认路径")
            return "/var/www/firefly" if "blog" in config_name else "/var/www/html"
        # 安全最佳实践：规范化路径，防止路径遍历攻击
        return os.path.realpath(path.strip())

    async def _is_firefly_blog(self, path: str) -> bool:
        """检查路径是否为 Firefly 博客项目"""
        if self.deploy_mode == DeployMode.REMOTE_BUILD and self.remote_executor:
            package_json = posixpath.join(path, "package.json")
            src_content = posixpath.join(path, "src", "content", "posts")
            astro_config = posixpath.join(path, "astro.config.mjs")
            
            matches = 0
            rc, _, _ = await self.remote_executor.run(f"test -f {package_json}", timeout=5)
            if rc == 0:
                matches += 1
            rc, _, _ = await self.remote_executor.run(f"test -d {src_content}", timeout=5)
            if rc == 0:
                matches += 1
            rc, _, _ = await self.remote_executor.run(f"test -f {astro_config}", timeout=5)
            if rc == 0:
                matches += 1
            return matches >= 2
        return is_firefly_blog_local(path)

    def _get_executor(self) -> CommandExecutor:
        """根据部署模式获取对应的命令执行器"""
        if self.deploy_mode == DeployMode.REMOTE_BUILD and self.remote_executor:
            return self.remote_executor
        return self.local_executor

    def _get_blog_root(self) -> str:
        """根据部署模式获取博客根目录"""
        if self.deploy_mode == DeployMode.REMOTE_BUILD:
            return self.remote_blog_root
        return self.blog_root

    async def check_environment(self) -> tuple[bool, str]:
        """检查构建环境（Node.js 和 pnpm）"""
        executor = self._get_executor()
        rc, out, err = await executor.run("node --version")
        if rc != 0:
            return False, f"未安装 Node.js: {err}"
        rc, out, err = await executor.run("pnpm --version")
        if rc != 0:
            return False, f"未安装 pnpm: {err}"
        return True, f"环境正常，Node.js: {out.strip()}"

    async def install_dependencies(self) -> tuple[bool, str]:
        """安装 pnpm 依赖（如果博客目录不存在则自动克隆）"""
        executor = self._get_executor()
        blog_root = self._get_blog_root()
        
        logger.info(f"[Build] 开始安装依赖，目录: {blog_root}")
        
        # 先检查博客目录是否存在，不存在则自动克隆
        blog_exists = True
        if self.deploy_mode == DeployMode.REMOTE_BUILD:
            rc, out, err = await executor.run(f"ls -la {blog_root}", timeout=10)
            if rc != 0:
                blog_exists = False
        else:
            if not os.path.exists(blog_root):
                blog_exists = False
        
        if not blog_exists:
            msg = await self._clone_blog_repo()
            if not msg.startswith("[OK]"):
                return False, msg
        
        # 检查 package.json 是否存在，不存在则尝试克隆
        package_json_path = os.path.join(blog_root, "package.json")
        package_exists = True
        if self.deploy_mode == DeployMode.REMOTE_BUILD:
            rc, out, err = await executor.run(f"ls -la {package_json_path}", timeout=10)
            if rc != 0:
                package_exists = False
        else:
            if not os.path.exists(package_json_path):
                package_exists = False
        
        if not package_exists:
            msg = await self._clone_blog_repo()
            if not msg.startswith("[OK]"):
                return False, msg
        
        rc, out, err = await executor.run("pnpm install", cwd=blog_root, timeout=DEPLOY_TIMEOUT)
        
        if rc != 0:
            error_details = []
            
            # 添加基本调试信息
            error_details.append(f"命令执行失败，返回码: {rc}")
            error_details.append(f"工作目录: {blog_root}")
            
            if err:
                # 提取关键错误信息
                error_lines = err.strip().split('\n')
                # 只显示最后几行关键错误
                if len(error_lines) > 10:
                    error_lines = error_lines[-10:]
                error_details.append("\n详细错误信息:")
                error_details.extend(error_lines)
            elif out:
                # 如果没有错误输出但有标准输出，也显示
                error_details.append("\n命令输出:")
                out_lines = out.strip().split('\n')
                if len(out_lines) > 10:
                    out_lines = out_lines[-10:]
                error_details.extend(out_lines)
            else:
                # 完全没有输出的情况
                error_details.append("\n命令无输出，可能的原因:")
                error_details.append("- pnpm 命令不存在或路径问题")
                error_details.append("- 权限不足")
                error_details.append("- 进程被意外终止")
            
            # 检查是否有警告信息
            if out:
                out_lines = out.strip().split('\n')
                warning_lines = [line for line in out_lines if 'WARN' in line or 'warning' in line.lower()]
                if warning_lines:
                    error_details.append("\n警告信息:")
                    error_details.extend(warning_lines[:5])
            
            error_summary = "\n".join(error_details)
            
            # 添加可能的解决方案
            solutions = [
                "\n\n可能的解决方案:",
                "1. 检查网络连接是否正常",
                "2. 尝试设置 npm/pnpm 镜像源:",
                "   pnpm config set registry https://registry.npmmirror.com",
                "3. 检查磁盘空间是否充足",
                "4. 尝试删除 node_modules 和 pnpm-lock.yaml 后重新安装",
                "5. 检查 Node.js 版本 >= 22",
                "6. 检查是否有权限访问博客目录",
                "7. 尝试手动运行 pnpm install 查看详细错误",
            ]
            
            return False, f"{error_summary}{''.join(solutions)}"
        
        # 检查输出中是否有警告
        if out and ('WARN' in out or 'warning' in out.lower()):
            return True, f"依赖安装成功（有警告）\n{out[-500:]}"
        
        return True, "依赖安装成功"

    async def _clone_blog_repo(self) -> str:
        """克隆 Firefly 博客仓库"""
        executor = self._get_executor()
        blog_root = self._get_blog_root()
        
        logger.info(f"[Build] 克隆 Firefly 博客仓库到: {blog_root}")
        
        # GitHub 仓库地址和镜像
        repo_url = "https://github.com/qiyueling2716/Firefly-Blog.git"
        mirror_url = "https://hubproxy.jiaozi.live/https://github.com/qiyueling2716/Firefly-Blog.git"
        
        # 先检查 git 是否安装
        rc, out, err = await executor.run("git --version", timeout=10)
        if rc != 0:
            return "[ERROR] 未安装 git，请先安装 git\n\n解决方案:\n- Ubuntu/Debian: sudo apt install git\n- CentOS/RHEL: sudo yum install git\n- macOS: brew install git\n- Windows: 下载安装 git"
        
        # 创建父目录
        if self.deploy_mode == DeployMode.REMOTE_BUILD:
            parent_dir = posixpath.dirname(blog_root)
            await executor.run(f"mkdir -p {parent_dir}", timeout=10)
        else:
            parent_dir = os.path.dirname(blog_root)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)
        
        # 检查目标目录是否已存在且非空
        dir_exists = False
        if self.deploy_mode == DeployMode.REMOTE_BUILD:
            rc, out, err = await executor.run(f"ls -la {blog_root}", timeout=10)
            dir_exists = rc == 0
        else:
            dir_exists = os.path.isdir(blog_root)
        
        if dir_exists:
            # 检查目录是否为空
            is_empty = False
            if self.deploy_mode == DeployMode.REMOTE_BUILD:
                rc, out, err = await executor.run(f"ls -A {blog_root} | wc -l", timeout=10)
                is_empty = rc == 0 and (out.strip() == "0" or not out.strip())
            else:
                is_empty = len(os.listdir(blog_root)) == 0
            
            if not is_empty:
                # 目录已存在且非空，检查是否已经是 Firefly 博客
                if await self._is_firefly_blog(blog_root):
                    logger.info(f"[Build] 目标目录已存在且是 Firefly 博客，跳过克隆")
                    return f"[OK] 目标目录已存在且是 Firefly 博客: {blog_root}"
                else:
                    # 目录存在但不是 Firefly 博客，询问是否覆盖
                    return f"[ERROR] 目标目录已存在但不是 Firefly 博客\n目录: {blog_root}\n请手动清理该目录后重试，或在配置中指定其他路径"
        
        # 尝试克隆仓库
        rc, out, err = await executor.run(f"git clone {repo_url} {blog_root}", timeout=120)
        if rc != 0:
            logger.warning(f"[Build] 主仓库克隆失败，尝试镜像: {err}")
            rc, out, err = await executor.run(f"git clone {mirror_url} {blog_root}", timeout=120)
            if rc != 0:
                return f"[ERROR] 克隆博客仓库失败\n错误信息: {err}\n\n可能的解决方案:\n1. 检查网络连接\n2. 尝试手动克隆: git clone {repo_url} {blog_root}\n3. 检查目标目录是否有写入权限"
        
        return f"[OK] 成功克隆 Firefly 博客仓库到 {blog_root}"

    async def build(self) -> tuple[bool, str]:
        """执行 pnpm build 构建博客

        遵循 allow_build_concurrent 配置：为 false 时同一时间只允许一个
        构建任务，避免多个构建进程竞争内存导致构建失败。
        """
        if not self.config.get("allow_build_concurrent", False):
            if self._build_lock.locked():
                return False, "已有构建任务正在进行，请等待其完成后再试（可通过 allow_build_concurrent 配置允许并发构建）"
            async with self._build_lock:
                return await self._do_build()
        return await self._do_build()

    async def _do_build(self) -> tuple[bool, str]:
        """执行实际构建（受并发锁保护，应用内存限制配置）"""
        executor = self._get_executor()
        blog_root = self._get_blog_root()
        memory_limit = self.config.get("build_memory_limit", 0)
        if memory_limit > 0:
            # 通过 NODE_OPTIONS 限制 Node.js 堆内存上限（单位 MB）
            command = f'NODE_OPTIONS="--max-old-space-size={memory_limit}" pnpm build'
        else:
            command = "pnpm build"
        rc, out, err = await executor.run(command, cwd=blog_root, timeout=BUILD_TIMEOUT)
        if rc != 0:
            return False, f"构建失败:\n{err}"
        return True, "构建成功"

    async def deploy(self) -> tuple[bool, str]:
        """部署构建产物到 Web 服务器"""
        if self.deploy_mode == DeployMode.LOCAL_ONLY:
            return await self._deploy_local()
        elif self.deploy_mode == DeployMode.LOCAL_BUILD:
            return await self._deploy_local_to_remote()
        elif self.deploy_mode == DeployMode.REMOTE_BUILD:
            return await self._deploy_remote()
        return False, "未知的部署模式"

    async def _deploy_local(self):
        """纯本地部署：复制 dist/ 到本地 web 目录"""
        local_dist = os.path.join(self.blog_root, "dist")
        if not os.path.exists(local_dist):
            return False, "构建产物不存在，请先构建"
        try:
            if os.path.exists(self.web_root):
                shutil.rmtree(self.web_root)
            shutil.copytree(local_dist, self.web_root)
            return True, f"已部署到 {self.web_root}"
        except Exception as e:
            logger.error(f"[BuildDeployManager] 本地部署失败: {e}")
            return False, "部署失败，请检查日志获取详细信息"

    async def _run_sshpass(self, inner_cmd: str, timeout: int = 300) -> tuple[int, str, str]:
        """安全执行 sshpass 命令，密码通过临时文件传递而非环境变量

        避免密码出现在进程列表（/proc/*/environ）中，同时防止密码中的
        特殊字符（如单引号）破坏 shell 命令结构。
        """
        import tempfile
        password = self.config.get("password", "")
        fd, temp_path = tempfile.mkstemp()
        try:
            os.write(fd, password.encode())
            os.close(fd)
            os.chmod(temp_path, 0o600)
            cmd = f"sshpass -f {shlex.quote(temp_path)} {inner_cmd}"
            return await self.local_executor.run(cmd, timeout=timeout)
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    async def _deploy_local_to_remote(self):
        """本地构建后通过 rsync/scp 部署到远端"""
        if not self.remote_executor:
            return False, "本地构建模式需要配置远程 SSH"

        local_dist = os.path.join(self.blog_root, "dist")
        if not os.path.exists(local_dist):
            return False, f"本地构建产物不存在，请先构建\n预期路径: {local_dist}"

        hostname = self.config.get("server_ip", "")
        username = self.config.get("username", "")
        port = self.config.get("server_port", 22)
        auth_type = self.config.get("auth_type", "key")

        # 优先使用 rsync 部署
        # 安全最佳实践：使用 StrictHostKeyChecking=yes 防止中间人攻击
        # 关闭时使用 accept-new（首次自动接受，后续变更拒绝），比完全禁用安全
        strict_checking = self.config.get("ssh_strict_host_key_checking", True)
        checking_opt = "yes" if strict_checking else "accept-new"
        ssh_opts = f"-p {port} -o StrictHostKeyChecking={checking_opt}"
        if auth_type == "key":
            key_path = self.config.get("private_key_path", "")
            if key_path and os.path.exists(key_path):
                ssh_opts += f" -i {key_path}"
            rsync_cmd = (
                f'rsync -avz --delete '
                f'-e "ssh {ssh_opts}" '
                f'{shlex.quote(f"{local_dist}/")} {shlex.quote(f"{username}@{hostname}:{self.remote_web_root}/")}'
            )
        else:
            # 密码认证：使用 sshpass -f 从临时文件读取密码，避免密码出现在进程列表中
            password = self.config.get("password", "")
            if not password:
                return False, "密码认证模式下未配置密码"
            rsync_cmd = (
                f'rsync -avz --delete '
                f'-e "ssh {ssh_opts}" '
                f'{shlex.quote(f"{local_dist}/")} {shlex.quote(f"{username}@{hostname}:{self.remote_web_root}/")}'
            )

        if auth_type == "password":
            rc, out, err = await self._run_sshpass(rsync_cmd, timeout=DEPLOY_TIMEOUT)
        else:
            rc, out, err = await self.local_executor.run(rsync_cmd, timeout=DEPLOY_TIMEOUT)
        if rc != 0:
            # rsync 失败，检查是否是本地路径问题
            logger.warning(f"rsync 失败: {err}")
            
            # 检查本地 dist 目录内容
            if os.path.exists(local_dist):
                dist_contents = os.listdir(local_dist)
                if not dist_contents:
                    return False, f"本地构建产物目录为空: {local_dist}\n请重新执行构建"
            
            # 回退到 scp
            logger.info("尝试使用 scp 部署")
            return await self._deploy_via_scp(local_dist, hostname, username, port, auth_type)

        return True, f"已部署到 {hostname}:{self.remote_web_root}"

    async def _deploy_via_scp(self, local_dist: str, hostname: str, username: str, port: int, auth_type: str) -> tuple[bool, str]:
        """通过 scp 部署（rsync 失败时的回退方案）"""
        # 安全最佳实践：使用 StrictHostKeyChecking=yes 防止中间人攻击
        strict_checking = self.config.get("ssh_strict_host_key_checking", True)
        checking_opt = "yes" if strict_checking else "accept-new"
        ssh_opts = f"-P {port} -o StrictHostKeyChecking={checking_opt}"

        if auth_type == "key":
            key_path = self.config.get("private_key_path", "")
            if key_path and os.path.exists(key_path):
                ssh_opts += f" -i {key_path}"
            scp_cmd = (
                f'scp -r {ssh_opts} '
                f'{shlex.quote(f"{local_dist}/*")} {shlex.quote(f"{username}@{hostname}:{self.remote_web_root}/")}'
            )
        else:
            password = self.config.get("password", "")
            if not password:
                return False, "密码认证模式下未配置密码"
            await self.remote_executor.run(f"rm -rf {self.remote_web_root}/*")
            scp_cmd = (
                f'scp -r {ssh_opts} '
                f'{shlex.quote(f"{local_dist}/*")} {shlex.quote(f"{username}@{hostname}:{self.remote_web_root}/")}'
            )

        if auth_type == "password":
            rc, out, err = await self._run_sshpass(scp_cmd, timeout=DEPLOY_TIMEOUT)
        else:
            rc, out, err = await self.local_executor.run(scp_cmd, timeout=DEPLOY_TIMEOUT)
        if rc != 0:
            return False, f"scp 部署失败:\n{err}"
        return True, f"已通过 scp 部署到 {hostname}:{self.remote_web_root}"

    async def _deploy_remote(self):
        """远端构建后复制 dist/ 到远端 Web 部署目录

        仓库位于 remote_blog_root，构建产物在 remote_blog_root/dist，
        部署目标为 remote_web_root（Web 服务器根目录）。
        """
        if not self.remote_executor:
            return False, "远程构建模式需要配置 SSH"

        # 安全校验：Web 目录不能与博客仓库目录相同，
        # 否则 rm -rf 会先删除仓库（包括 dist）再复制，导致部署失败
        if posixpath.normpath(self.remote_web_root) == posixpath.normpath(self.remote_blog_root):
            return False, (
                "remote_web_root 与 remote_blog_root 配置相同，无法部署。"
                "请将 remote_web_root 设置为独立的 Web 部署目录（例如 /var/www/html）"
            )

        rc, out, err = await self.remote_executor.run(
            f"rm -rf {self.remote_web_root}/* && cp -r {self.remote_blog_root}/dist/* {self.remote_web_root}/",
            timeout=60,
        )
        if rc != 0:
            return False, f"远端部署失败:\n{err}"
        return True, f"已部署到远端 {self.remote_web_root}"

    async def check_dependencies_installed(self) -> bool:
        """检查 node_modules 是否已安装"""
        blog_root = self._get_blog_root()
        if self.deploy_mode == DeployMode.REMOTE_BUILD and self.remote_executor:
            node_modules_path = posixpath.join(blog_root, "node_modules")
            rc, _, _ = await self.remote_executor.run(f"test -d {node_modules_path}")
            return rc == 0
        else:
            node_modules_path = os.path.join(blog_root, "node_modules")
            return os.path.exists(node_modules_path)

