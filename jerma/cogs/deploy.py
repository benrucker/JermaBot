import asyncio
import hashlib
import hmac
import json
import os
import re
import subprocess
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiohttp import web
from discord.ext import commands

from jermabot import JermaBot


async def setup(bot: JermaBot):
    if not bot.track:
        return
    await bot.add_cog(Deploy(bot))


class Deploy(commands.Cog):
    _TRACK_BRANCH = {
        'beta': 'develop',
        'production': 'release',
    }

    _NON_CODE_EXTS = {
        '.md', '.txt', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico',
        '.wav', '.mp3', '.ogg', '.flac', '.bmp', '.svg',
    }

    # Matches a top-level cog file, e.g. "jerma/cogs/admin.py".
    # Files under jerma/cogs/utils/ or __init__ are NOT matched.
    _COGS_FILE_RE = re.compile(r'^jerma/cogs/([^/]+)\.py$')

    def __init__(self, bot: JermaBot):
        self.bot = bot
        self.track: str = bot.track  # type: ignore[assignment]
        self.branch: str = self._TRACK_BRANCH[self.track]
        self._secret: str = os.environ.get('GITHUB_WEBHOOK_SECRET', '')
        self._webhook_task: asyncio.Task | None = None
        self._runner: web.AppRunner | None = None
        self._restart_task: asyncio.Task | None = None

    async def cog_load(self):
        self._webhook_task = asyncio.create_task(self._run_webhook_server())

    async def cog_unload(self):
        if self._webhook_task:
            self._webhook_task.cancel()
            try:
                await self._webhook_task
            except asyncio.CancelledError:
                pass
        if self._runner:
            await self._runner.cleanup()
        if self._restart_task and not self._restart_task.done():
            self._restart_task.cancel()
            try:
                await self._restart_task
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------------
    # Webhook server
    # ------------------------------------------------------------------

    async def _run_webhook_server(self):
        port = int(os.environ.get('WEBHOOK_PORT', '9000'))

        app = web.Application()
        app.router.add_post('/webhook', self._handle_webhook)

        runner = web.AppRunner(app)
        self._runner = runner
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        print(f'[deploy] Webhook server on :{port} (track={self.track}, branch={self.branch})')

        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await runner.cleanup()
            self._runner = None
            raise

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        body = await request.read()

        if self._secret:
            sig = request.headers.get('X-Hub-Signature-256', '')
            expected = 'sha256=' + hmac.new(self._secret.encode(), body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, expected):
                return web.Response(status=403, text='Forbidden')

        if request.headers.get('X-GitHub-Event', '') != 'push':
            return web.Response(status=200, text='ok')

        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.Response(status=400, text='Bad JSON')

        asyncio.create_task(self._handle_push(payload.get('ref', '')))
        return web.Response(status=200, text='ok')

    # ------------------------------------------------------------------
    # Push handling
    # ------------------------------------------------------------------

    async def _handle_push(self, ref: str):
        pushed_branch = ref.removeprefix('refs/heads/')
        if pushed_branch != self.branch:
            return

        print(f'[deploy] Push to {pushed_branch}, pulling...')
        changed = self._git_pull_and_diff()
        if changed is None:
            print('[deploy] Already up to date or pull failed.')
            return

        action = self._classify_changes(changed)
        print(f'[deploy] action={action} files={changed}')

        if action == 'none':
            return
        elif action == 'reload':
            await self._reload_cogs(self._affected_cog_extensions(changed))
        elif action == 'restart':
            if self.track == 'production':
                self._schedule_4am_restart()
            else:
                await self._restart()

    async def _reload_cogs(self, extensions: list[str]):
        for ext in extensions:
            try:
                await self.bot.reload_extension(ext)
                print(f'[deploy] Reloaded {ext}')
            except Exception as e:
                print(f'[deploy] Failed to reload {ext}: {e}')

    def _schedule_4am_restart(self):
        if self._restart_task and not self._restart_task.done():
            print('[deploy] Restart already scheduled, skipping.')
            return
        self._restart_task = asyncio.create_task(self._wait_until_4am_and_restart())

    async def _wait_until_4am_and_restart(self):
        pacific = ZoneInfo('America/Los_Angeles')
        now = datetime.now(pacific)
        target = now.replace(hour=4, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        delay = (target - now).total_seconds()
        print(f'[deploy] Production restart in {delay:.0f}s (4am Pacific)')
        await asyncio.sleep(delay)
        # TODO: Wait for in-flight operations (active voice sessions, TTS jobs,
        #       agent tasks) to finish before restarting.
        await self._restart()

    async def _restart(self):
        print('[deploy] Restarting bot...')
        admin = self.bot.get_cog('Admin')
        if admin:
            await admin.shutdown()  # type: ignore[attr-defined]
        else:
            await self.bot.close()

    # ------------------------------------------------------------------
    # Diff analysis
    # ------------------------------------------------------------------

    @staticmethod
    def _git_pull_and_diff() -> list[str] | None:
        """Pull and return changed file paths, or None on failure / no update."""
        rev = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True)
        if rev.returncode != 0:
            print(f'[deploy] git rev-parse failed: {rev.stderr}')
            return None
        old_head = rev.stdout.strip()

        pull = subprocess.run(['git', 'pull'], capture_output=True, text=True)
        if pull.returncode != 0:
            print(f'[deploy] git pull failed: {pull.stderr}')
            return None
        if 'Already up to date' in pull.stdout:
            return None

        diff = subprocess.run(
            ['git', 'diff', '--name-only', old_head, 'HEAD'],
            capture_output=True, text=True,
        )
        return [f for f in diff.stdout.strip().split('\n') if f]

    @classmethod
    def _classify_changes(cls, files: list[str]) -> str:
        """Return 'none', 'reload', or 'restart'."""
        code_files = [f for f in files if not cls._is_non_code(f)]
        if not code_files:
            return 'none'
        if all(cls._is_top_level_cog(f) for f in code_files):
            return 'reload'
        return 'restart'

    @classmethod
    def _affected_cog_extensions(cls, files: list[str]) -> list[str]:
        exts = []
        for f in files:
            m = cls._COGS_FILE_RE.match(f)
            if m and m.group(1) != '__init__':
                exts.append('cogs.' + m.group(1))
        return exts

    @classmethod
    def _is_non_code(cls, path: str) -> bool:
        return os.path.splitext(path)[1].lower() in cls._NON_CODE_EXTS

    @classmethod
    def _is_top_level_cog(cls, path: str) -> bool:
        m = cls._COGS_FILE_RE.match(path)
        return bool(m) and m.group(1) != '__init__'
