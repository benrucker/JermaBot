import ast
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
        # TODO: narrow JermaBot.track to str (not str | None) so this assignment is type-safe
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
        result = self._git_pull_and_diff()
        if result is None:
            print('[deploy] Already up to date or pull failed.')
            return

        files, diff = result
        action = self._classify_changes(files, diff)
        print(f'[deploy] action={action} files={files}')

        if action == 'none':
            return
        elif action == 'reload':
            await self._reload_cogs(self._get_affected_cog_extensions(files))
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
            # TODO: type the Admin cog properly so shutdown() is visible without suppression
            await admin.shutdown()  # type: ignore[attr-defined]
        else:
            await self.bot.close()

    # ------------------------------------------------------------------
    # Diff analysis
    # ------------------------------------------------------------------

    @staticmethod
    def _git_pull_and_diff() -> tuple[list[str], str] | None:
        """Pull; return (changed_file_paths, unified_diff) or None on failure / no update."""
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

        names = subprocess.run(
            ['git', 'diff', '--name-only', old_head, 'HEAD'],
            capture_output=True, text=True,
        )
        files = [f for f in names.stdout.strip().split('\n') if f]

        full_diff = subprocess.run(
            ['git', 'diff', '--unified=0', old_head, 'HEAD'],
            capture_output=True, text=True,
        )
        return files, full_diff.stdout

    @classmethod
    def _classify_changes(cls, files: list[str], diff: str) -> str:
        """Return 'none', 'reload', or 'restart'."""
        code_files = [f for f in files if not cls._is_non_code(f)]
        if not code_files:
            return 'none'
        if all(cls._is_top_level_cog(f) for f in code_files):
            file_diffs = cls._split_diff_by_file(diff)
            if all(cls._check_changes_within_cog_class(f, file_diffs.get(f, '')) for f in code_files):
                return 'reload'
        return 'restart'

    @classmethod
    def _get_affected_cog_extensions(cls, files: list[str]) -> list[str]:
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

    @staticmethod
    def _split_diff_by_file(diff: str) -> dict[str, str]:
        """Parse a unified diff into a {git_path: diff_block} mapping."""
        result: dict[str, str] = {}
        current_file: str | None = None
        current_lines: list[str] = []
        for line in diff.split('\n'):
            if line.startswith('diff --git '):
                if current_file is not None:
                    result[current_file] = '\n'.join(current_lines)
                # "diff --git a/jerma/cogs/admin.py b/jerma/cogs/admin.py"
                parts = line.split(' ')
                b_path = parts[-1]
                current_file = b_path[2:] if b_path.startswith('b/') else b_path
                current_lines = [line]
            elif current_file is not None:
                current_lines.append(line)
        if current_file is not None:
            result[current_file] = '\n'.join(current_lines)
        return result

    @staticmethod
    def _parse_diff_hunks(diff_block: str) -> list[tuple[int, int]]:
        """Return (start, end) line ranges added/modified in the new file version."""
        ranges = []
        for line in diff_block.split('\n'):
            if not line.startswith('@@'):
                continue
            m = re.search(r'\+(\d+)(?:,(\d+))?', line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) is not None else 1
                if count > 0:
                    ranges.append((start, start + count - 1))
        return ranges

    @staticmethod
    def _find_cog_class_ranges(source: str) -> list[tuple[int, int]]:
        """Return (start_line, end_line) for each class that inherits from commands.Cog."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        ranges = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for base in node.bases:
                is_cog = (
                    (isinstance(base, ast.Attribute) and base.attr == 'Cog') or
                    (isinstance(base, ast.Name) and base.id == 'Cog')
                )
                if is_cog:
                    ranges.append((node.lineno, node.end_lineno or node.lineno))
                    break
        return ranges

    @classmethod
    def _check_changes_within_cog_class(cls, git_path: str, diff_block: str) -> bool:
        """Return True only if every changed line in the file falls inside a Cog subclass."""
        changed_ranges = cls._parse_diff_hunks(diff_block)
        if not changed_ranges:
            return True

        # git_path is repo-root-relative ("jerma/cogs/admin.py");
        # the bot runs from jerma/, so strip the leading "jerma/" to open the file.
        cwd_path = git_path.removeprefix('jerma/')
        try:
            with open(cwd_path) as f:
                source = f.read()
        except OSError:
            return False

        cog_ranges = cls._find_cog_class_ranges(source)
        if not cog_ranges:
            return False

        return all(
            any(cog_start <= change_start and change_end <= cog_end
                for cog_start, cog_end in cog_ranges)
            for change_start, change_end in changed_ranges
        )
