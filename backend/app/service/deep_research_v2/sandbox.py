"""生成代码的容器执行边界；不可用时失败，不回退到宿主执行。"""
import asyncio
import base64
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4


BOOTSTRAP = Path(__file__).with_name('sandbox_worker.py').read_text(encoding='utf-8')
OUTPUT_LIMIT = 8 * 1024 * 1024


def failure(message):
    return {'success': False, 'output': '', 'error': message, 'charts': [], 'sandbox': 'docker'}


async def execute_isolated(code, *, image=None, timeout=60, memory_mb=512):
    """仅传递代码，无宿主挂载、宿主环境变量或网络；每次执行销毁容器。"""
    if len(code.encode('utf-8')) > 256 * 1024:
        return failure('代码超过 256 KB 限制')
    image = image or os.environ.get('RESEARCH_SANDBOX_IMAGE', 'industry-research-sandbox:local')
    name = 'research-sandbox-' + uuid4().hex
    flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    process = None

    async def read_bounded(stream, limit):
        chunks, size = [], 0
        while chunk := await stream.read(16384):
            size += len(chunk)
            if size > limit:
                raise ValueError('沙箱输出超过上限')
            chunks.append(chunk)
        return b''.join(chunks)

    async def communicate():
        process.stdin.write(json.dumps({'code': code}, ensure_ascii=False).encode('utf-8'))
        await process.stdin.drain()
        process.stdin.close()
        readers = [asyncio.create_task(read_bounded(process.stdout, OUTPUT_LIMIT)),
                   asyncio.create_task(read_bounded(process.stderr, 65536))]
        try:
            out, err = await asyncio.gather(*readers)
            await process.wait()
            if process.returncode:
                return failure(f'沙箱执行失败（退出码 {process.returncode}）：' + err.decode('utf-8', 'replace')[-2000:])
            result = json.loads(out)
            if not isinstance(result, dict) or not isinstance(result.get('success'), bool):
                raise ValueError('沙箱返回格式无效')
            charts = result.get('charts', [])
            if not isinstance(charts, list) or len(charts) > 4:
                raise ValueError('沙箱图表数量无效')
            for chart in charts:
                if not isinstance(chart, str) or not base64.b64decode(chart, validate=True).startswith(b'\x89PNG\r\n\x1a\n'):
                    raise ValueError('沙箱只能返回 PNG 图表')
            return {'success': result['success'], 'output': str(result.get('output', ''))[:32768],
                    'error': str(result['error'])[:4000] if result.get('error') else None,
                    'charts': charts, 'sandbox': 'docker'}
        finally:
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)

    try:
        process = await asyncio.create_subprocess_exec(
            'docker', 'run', '--rm', '--pull', 'never', '--name', name, '-i',
            '--network', 'none', '--read-only', '--user', '65534:65534',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--pids-limit', '32', '--memory', f'{memory_mb}m', '--memory-swap', f'{memory_mb}m',
            '--cpus', '1', '--ulimit', 'nofile=64:64', '--ulimit', 'core=0:0',
            '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=67108864,mode=1777',
            '--workdir', '/tmp', '--env', 'MPLCONFIGDIR=/tmp/matplotlib',
            '--env', 'OPENBLAS_NUM_THREADS=1', '--env', 'OMP_NUM_THREADS=1',
            image, 'python', '-I', '-u', '-c', BOOTSTRAP,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, **flags)
        return await asyncio.wait_for(communicate(), timeout)
    except asyncio.TimeoutError:
        return failure(f'代码执行超过 {timeout} 秒，容器已终止')
    except (OSError, ValueError, BrokenPipeError) as exc:
        return failure(f'沙箱不可用或执行失败：{exc}')
    finally:
        if process is not None:
            # 仅清理本次随机命名的容器；取消、超时和正常结束均走同一出口。
            cleanup = await asyncio.create_subprocess_exec('docker', 'rm', '-f', name,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, **flags)
            try:
                await asyncio.wait_for(cleanup.wait(), 10)
            except asyncio.TimeoutError:
                cleanup.kill()
                await cleanup.wait()
            if process.returncode is None:
                process.kill()
            # Windows Proactor 还需收束管道，单独 wait() 会遗留 transport。
            await process.communicate()
