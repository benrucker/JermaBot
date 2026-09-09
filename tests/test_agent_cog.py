"""Thread recognition and recovery: derived from Discord, never from local
state.

Discord objects are mocked at the boundary only (spec'd so the cog's
isinstance checks are the real ones); nothing here talks to Discord.
"""
import re
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.agent import (
    HISTORY_HEADING,
    IMAGE_LINK_MAX_BYTES,
    TIMEOUT_NOTICE,
    Agent,
    build_thread_history,
    fetch_image_link,
    parse_pr_announcements,
)
from cogs.utils.agent_service import TaskReport

BOT_ID = 111
OWNER_ID = 222
STRANGER_ID = 333
THREAD_ID = 999
PARENT_ID = 500
MESSAGE_ID = 700
NEW_THREAD_ID = 700  # a thread takes the id of the message it grew from


def http_error(cls, status: int):
    return cls(MagicMock(status=status, reason='nope'), 'nope')


class _NoTyping:
    """discord.abc.Messageable.typing()'s context manager, doing nothing."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class FakeBot:
    """Just enough bot for the cog: identity, ownership, channel fetches."""

    def __init__(self):
        self.user = SimpleNamespace(id=BOT_ID)
        self.channels: dict[int, object] = {}
        self.fetch_calls: list[int] = []

    async def is_owner(self, user) -> bool:
        return getattr(user, 'id', None) == OWNER_ID

    async def fetch_channel(self, channel_id):
        self.fetch_calls.append(channel_id)
        return self.channels[channel_id]

    async def get_context(self, message):
        return SimpleNamespace(valid=False)


def make_starter(content: str, author_id: int = OWNER_ID):
    return SimpleNamespace(content=content,
                           author=SimpleNamespace(id=author_id),
                           id=THREAD_ID, attachments=[], embeds=[],
                           created_at=datetime(2026, 8, 16, 14, 0))


def make_thread(bot: FakeBot, owner_id: int = BOT_ID,
                starter=None, missing_starter: bool = False,
                fetch_error: Exception | None = None):
    parent = MagicMock(spec=discord.TextChannel)
    parent.id = PARENT_ID
    if fetch_error is not None:
        parent.fetch_message = AsyncMock(side_effect=fetch_error)
    elif missing_starter:
        parent.fetch_message = AsyncMock(
            side_effect=http_error(discord.NotFound, 404))
    else:
        parent.fetch_message = AsyncMock(
            return_value=starter or make_starter(f'<@{BOT_ID}> fix the thing'))

    thread = MagicMock(spec=discord.Thread)
    thread.id = THREAD_ID
    thread.owner_id = owner_id
    thread.parent = parent
    thread.parent_id = PARENT_ID
    bot.channels[THREAD_ID] = thread
    return thread


@pytest.fixture
def cog(agent_dirs):
    bot = FakeBot()
    return Agent(bot)


async def test_a_bot_thread_started_by_an_owner_ping_is_recognized(cog):
    thread = make_thread(cog.bot)

    assert await cog._is_agent_thread(thread)
    # With no conversation on this host at all: recognition came from
    # Discord alone, which is the whole requirement.
    assert not cog.service.has_conversation(THREAD_ID)
    thread.parent.fetch_message.assert_awaited_once_with(THREAD_ID)


async def test_recognition_is_cached_per_thread(cog):
    thread = make_thread(cog.bot)

    assert await cog._is_agent_thread(thread)
    assert await cog._is_agent_thread(thread)

    assert thread.parent.fetch_message.await_count == 1


async def test_a_known_conversation_skips_the_fetch(cog):
    thread = make_thread(cog.bot)
    cog.service.conversations[THREAD_ID] = object()

    assert await cog._is_agent_thread(thread)

    thread.parent.fetch_message.assert_not_awaited()


async def test_someone_elses_thread_is_not_ours(cog):
    thread = make_thread(cog.bot, owner_id=STRANGER_ID)

    assert not await cog._is_agent_thread(thread)

    thread.parent.fetch_message.assert_not_awaited()


async def test_a_thread_the_bot_made_for_something_else_is_not_ours(cog):
    """The bot's own threads that did not grow from an owner's ping — the
    starter has to be the request itself."""
    thread = make_thread(cog.bot,
                         starter=make_starter('welcome to the thread'))

    assert not await cog._is_agent_thread(thread)


async def test_a_ping_from_a_stranger_is_not_ours(cog):
    thread = make_thread(cog.bot,
                         starter=make_starter(f'<@{BOT_ID}> do my bidding',
                                              author_id=STRANGER_ID))

    assert not await cog._is_agent_thread(thread)


async def test_a_thread_without_a_starter_is_not_ours(cog):
    thread = make_thread(cog.bot, missing_starter=True)

    assert not await cog._is_agent_thread(thread)


async def test_an_uncached_thread_is_fetched(cog):
    """A message in an archived thread can arrive before the thread is back
    in the library's cache; all the event carries is a stub."""
    thread = make_thread(cog.bot)
    partial = MagicMock(spec=discord.PartialMessageable)
    partial.id = THREAD_ID
    message = SimpleNamespace(channel=partial)

    resolved = await cog._resolve_channel(message)

    assert resolved is thread
    assert cog.bot.fetch_calls == [THREAD_ID]


async def test_a_cached_channel_is_not_fetched(cog):
    thread = make_thread(cog.bot)
    message = SimpleNamespace(channel=thread)

    assert await cog._resolve_channel(message) is thread
    assert cog.bot.fetch_calls == []


def make_message(channel, content: str, author_id: int = OWNER_ID):
    return SimpleNamespace(
        id=MESSAGE_ID,
        channel=channel,
        content=content,
        attachments=[],
        embeds=[],
        author=SimpleNamespace(id=author_id, bot=False),
        reply=AsyncMock(),
        create_thread=AsyncMock(),
    )


def make_text_channel():
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = PARENT_ID
    channel.typing = MagicMock(side_effect=_NoTyping)
    channel.send = AsyncMock()
    return channel


@pytest.fixture
def handled(cog, monkeypatch):
    """Records what on_message decided to run, instead of running it."""
    calls = []

    async def fake_handle(message, source, prompt, images, inline_urls):
        calls.append((source, prompt))

    monkeypatch.setattr(cog, '_handle_prompt', fake_handle)
    return calls


async def test_a_message_in_an_archived_agent_thread_runs_a_turn(cog, handled):
    thread = make_thread(cog.bot)
    partial = MagicMock(spec=discord.PartialMessageable)
    partial.id = THREAD_ID

    await cog.on_message(make_message(partial, 'and also fix that'))

    # The turn runs against the fetched thread, not the stub.
    assert handled == [(thread, 'and also fix that')]


async def test_an_unpinged_message_elsewhere_is_ignored(cog, handled):
    channel = make_text_channel()

    await cog.on_message(make_message(channel, 'just chatting'))

    assert handled == []
    assert cog.bot.fetch_calls == []


async def test_a_message_in_someone_elses_thread_is_ignored(cog, handled):
    thread = make_thread(cog.bot, owner_id=STRANGER_ID)

    await cog.on_message(make_message(thread, 'just chatting'))

    assert handled == []


async def test_a_ping_still_starts_a_conversation_anywhere(cog, handled):
    channel = make_text_channel()

    await cog.on_message(make_message(channel, f'<@{BOT_ID}> fix the thing'))

    assert handled == [(channel, 'fix the thing')]


async def test_a_stranger_is_ignored_in_an_agent_thread(cog, handled):
    thread = make_thread(cog.bot)

    await cog.on_message(make_message(thread, 'let me in',
                                      author_id=STRANGER_ID))

    assert handled == []
    thread.parent.fetch_message.assert_not_awaited()


async def test_a_discord_failure_is_reported_not_swallowed(cog, handled):
    """A recognition fetch can fail for reasons that are not an answer:
    Discord down, permissions changed, a timeout. The owner's message may
    not vanish into a console traceback over it."""
    thread = make_thread(cog.bot,
                         fetch_error=http_error(discord.Forbidden, 403))
    message = make_message(thread, 'and also fix that')

    await cog.on_message(message)

    assert handled == []
    message.reply.assert_awaited_once()
    assert 'nope' in message.reply.await_args.args[0]
    # Nothing was learned, so nothing is remembered: the next message in
    # this thread asks Discord again.
    assert THREAD_ID not in cog._agent_threads


async def test_a_failure_resolving_the_channel_is_reported(cog, handled):
    partial = MagicMock(spec=discord.PartialMessageable)
    partial.id = THREAD_ID
    cog.bot.channels.clear()  # fetch_channel will raise KeyError otherwise
    cog.bot.fetch_channel = AsyncMock(
        side_effect=http_error(discord.DiscordServerError, 503))
    message = make_message(partial, 'and also fix that')

    await cog.on_message(message)

    assert handled == []
    message.reply.assert_awaited_once()


async def test_a_new_thread_gets_the_longest_archive(cog, monkeypatch):
    """R1.4: the bot sets the longest auto-archive Discord allows, and the
    thread it just made is known to be ours without a fetch."""
    channel = make_text_channel()
    message = make_message(channel, f'<@{BOT_ID}> fix the thing')
    thread = MagicMock(spec=discord.Thread)
    thread.id = NEW_THREAD_ID
    thread.typing = MagicMock(side_effect=_NoTyping)
    thread.send = AsyncMock()
    message.create_thread = AsyncMock(return_value=thread)

    async def fake_run(key, prompt, on_progress, images=(), reconstruct=None):
        assert key == MESSAGE_ID  # the thread will share the message's id
        return TaskReport(answer='done', pull_requests=[], timed_out=False)

    monkeypatch.setattr(cog.service, 'run', fake_run)

    await cog.on_message(message)

    message.create_thread.assert_awaited_once()
    assert message.create_thread.await_args.kwargs['auto_archive_duration'] \
        == 10080
    assert cog._agent_threads[NEW_THREAD_ID] is True
    thread.send.assert_awaited_once_with('done')


# --- pull request announcements, read back out of a thread (R3.3) ------

DEMO_PR = 'https://github.com/benrucker/JermaBot/pull/1'
OTHER_PR = 'https://github.com/ecfidler/shigure-js/pull/2'


def test_both_announcement_shapes_are_read():
    urls = parse_pr_announcements([
        'Working on it.',
        f'Pull request for **jermabot**: {DEMO_PR}',
        f'Updated the pull request for **jermabot**: {DEMO_PR}',
    ])

    assert urls == {'jermabot': DEMO_PR}


def test_the_latest_announcement_per_repo_wins_and_ends_the_dict():
    """A conversation whose branch was merged away opens a second pull
    request, and the branch to recover is the newest one's."""
    replaced = 'https://github.com/benrucker/JermaBot/pull/3'
    urls = parse_pr_announcements([
        f'Pull request for **jermabot**: {DEMO_PR}',
        f'Pull request for **shigure-js**: {OTHER_PR}',
        f'Pull request for **jermabot**: {replaced}',
    ])

    assert urls == {'shigure-js': OTHER_PR, 'jermabot': replaced}
    assert list(urls.values())[-1] == replaced


def test_the_agents_own_prose_is_not_an_announcement():
    urls = parse_pr_announcements([
        f'I opened a pull request for **jermabot**: see {DEMO_PR} for it.',
        f'Pull request for jermabot: {DEMO_PR}',
    ])

    assert urls == {}


# --- recovering a conversation this host has no record of --------------


def make_history(thread, *contents, author_id: int = BOT_ID):
    """The thread's own messages, oldest first, as history() yields them."""
    messages = [SimpleNamespace(content=content,
                                author=SimpleNamespace(id=author_id),
                                id=MESSAGE_ID, attachments=[], embeds=[],
                                created_at=datetime(2026, 8, 16, 14, 3))
                for content in contents]

    def history(**_):
        async def iterator():
            for message in messages:
                yield message
        return iterator()

    thread.history = history


@pytest.fixture
def recovered(cog, monkeypatch):
    """Records what the cog handed the service to recover from."""
    calls = []

    async def fake_recover(key, starter_prompt, pr_urls):
        calls.append((key, starter_prompt, pr_urls))
        return bool(pr_urls)

    monkeypatch.setattr(cog.service, 'recover', fake_recover)
    return calls


async def test_a_threads_own_announcements_recover_its_pull_requests(
        cog, recovered):
    thread = make_thread(cog.bot)
    make_history(thread,
                 'On it.',
                 f'Pull request for **jermabot**: {DEMO_PR}')

    await cog._recover_conversation(thread, THREAD_ID)

    assert recovered == [(THREAD_ID, 'fix the thing', {'jermabot': DEMO_PR})]


async def test_a_thread_without_announcements_is_looked_up_by_its_starter(
        cog, recovered):
    """Nothing was ever announced, so the request that started the thread
    is all GitHub can be searched by."""
    thread = make_thread(cog.bot)
    make_history(thread, 'Here is your answer.')

    await cog._recover_conversation(thread, THREAD_ID)

    assert recovered == [(THREAD_ID, 'fix the thing', {})]


async def test_a_known_conversation_is_not_recovered_again(cog, recovered):
    thread = make_thread(cog.bot)
    make_history(thread, f'Pull request for **jermabot**: {DEMO_PR}')
    cog.service.conversations[THREAD_ID] = object()

    await cog._recover_conversation(thread, THREAD_ID)

    assert recovered == []


async def test_an_unrelated_thread_is_not_recovered(cog, recovered):
    """A ping in someone else's thread: no conversation of ours to put
    back, and its history is none of our business."""
    thread = make_thread(cog.bot, owner_id=STRANGER_ID)
    make_history(thread, f'Pull request for **jermabot**: {DEMO_PR}')

    await cog._recover_conversation(thread, THREAD_ID)

    assert recovered == []


async def test_an_unreadable_thread_says_so_instead_of_starting_over(
        cog, monkeypatch, recovered):
    """Reading the thread is how its branch is found; running anyway would
    quietly abandon the pull request the conversation already has."""
    thread = make_thread(cog.bot)
    thread.typing = MagicMock(side_effect=_NoTyping)
    thread.send = AsyncMock()

    def history(**_):
        raise http_error(discord.HTTPException, 503)

    thread.history = history
    ran = []
    monkeypatch.setattr(cog.service, 'run',
                        lambda *a, **k: ran.append(a))

    await cog._handle_prompt(make_message(thread, 'and also fix that'),
                             thread, 'and also fix that')

    assert ran == []
    posted = thread.send.await_args_list[0].args[0]
    assert "couldn't read this thread's history" in posted


# --- the thread as prior history (R2c) ---------------------------------


def make_attachment(filename: str, data: bytes | None = None,
                    content_type: str = 'image/png'):
    """An attachment that either still downloads or is gone from Discord."""
    attachment = MagicMock(spec=discord.Attachment)
    attachment.filename = filename
    attachment.content_type = content_type
    attachment.read = AsyncMock(
        return_value=data) if data is not None else AsyncMock(
            side_effect=http_error(discord.NotFound, 404))
    return attachment


def make_embed(url: str):
    """The embed Discord builds for a pasted image link."""
    embed = MagicMock(spec=discord.Embed)
    embed.type = 'image'
    embed.url = url
    embed.image = None
    return embed


def make_history_message(content: str, author_id: int = OWNER_ID,
                         minute: int = 2, message_id: int = MESSAGE_ID,
                         attachments=(), embeds=()):
    return SimpleNamespace(
        id=message_id,
        content=content,
        attachments=list(attachments),
        embeds=list(embeds),
        author=SimpleNamespace(id=author_id),
        created_at=datetime(2026, 8, 16, 14, minute))


async def refuse_fetch(url):
    """A link that will not come back: somebody else's web server."""
    raise ValueError('HTTP 404')


async def build(*messages, fetch=refuse_fetch):
    return await build_thread_history(messages, BOT_ID, OWNER_ID, fetch)


async def test_the_thread_comes_back_as_labelled_turns_in_order():
    history = await build(
        make_history_message('fix the thing', minute=2),
        make_history_message('Here is your answer.', author_id=BOT_ID,
                             minute=3),
        make_history_message('and also fix that', minute=4))

    assert history.text == (
        f"""{HISTORY_HEADING}

[2026-08-16 14:02 UTC] Owner:
fix the thing

[2026-08-16 14:03 UTC] You (agent):
Here is your answer.

[2026-08-16 14:04 UTC] Owner:
and also fix that""")
    assert history.images == []


async def test_the_harness_speaks_as_the_harness_not_as_the_agent():
    """R2c.1b: the bot posts things on its own behalf, and an agent told
    they were its own words would answer for them next turn."""
    history = await build(
        make_history_message('fix the thing'),
        make_history_message(f'Pull request for **jermabot**: {DEMO_PR}',
                             author_id=BOT_ID, minute=3),
        make_history_message('-# _Reloading thread history. Some context '
                             'might be lost._',
                             author_id=BOT_ID, minute=4),
        make_history_message(TIMEOUT_NOTICE, author_id=BOT_ID, minute=5),
        make_history_message('Workspace error: the push failed',
                             author_id=BOT_ID, minute=6))

    assert history.text.split('\n\n')[2:] == [
        f'[2026-08-16 14:03 UTC] [harness] Pull request opened for '
        f'jermabot: {DEMO_PR}',
        '[2026-08-16 14:04 UTC] [harness] Reloading thread history. Some '
        'context might be lost.',
        '[2026-08-16 14:05 UTC] [harness] The turn timed out and published '
        'what it had finished.',
        '[2026-08-16 14:06 UTC] [harness] The turn failed: the push failed',
    ]


async def test_an_answer_with_an_announcement_keeps_both_apart():
    """One message can be the agent talking and the harness announcing."""
    history = await build(make_history_message(
        f'Done.\nUpdated the pull request for **jermabot**: {DEMO_PR}',
        author_id=BOT_ID, minute=3))

    assert history.text.split('\n\n')[1:] == [
        '[2026-08-16 14:03 UTC] You (agent):\nDone.',
        f'[2026-08-16 14:03 UTC] [harness] Pull request updated for '
        f'jermabot: {DEMO_PR}',
    ]


async def test_images_come_back_downloaded_and_lost_ones_are_named():
    """R2c.2: the agent saw these, so they are fetched again; one Discord
    has stopped serving is a gap the agent should know about rather than a
    message that silently changed."""
    history = await build(make_history_message(
        'what is wrong with this?',
        attachments=[make_attachment('shot.png', b'PNG'),
                     make_attachment('gone.png'),
                     make_attachment('notes.txt', b'text',
                                     content_type='text/plain')]))

    assert history.images == [(f'{MESSAGE_ID}-shot.png', b'PNG')]
    lines = history.text.split('\n\n')[1].splitlines()
    assert lines[:3] == [
        '[2026-08-16 14:02 UTC] Owner:',
        'what is wrong with this?',
        f'[image attachment: {MESSAGE_ID}-shot.png]',
    ]
    # The text file is not an image and no business of the agent's; the
    # image Discord has stopped serving is a named gap, not a message that
    # quietly changed.
    assert len(lines) == 4
    assert lines[3].startswith('[image attachment gone.png: Discord no '
                               'longer has this file (')


async def test_a_stranger_in_the_thread_is_not_the_conversation():
    history = await build(
        make_history_message('fix the thing'),
        make_history_message('lol', author_id=STRANGER_ID, minute=3))

    assert 'lol' not in history.text


async def test_an_empty_thread_rebuilds_nothing():
    """Nothing to tell the agent; the service starts the session clean."""
    history = await build(make_history_message('hi', author_id=STRANGER_ID))

    assert history.text == ''


async def test_the_starter_leads_and_the_new_message_is_left_out(cog):
    """The message an agent thread grew from lives in the parent channel,
    and the one being answered belongs at the end of the prompt, not in
    the history."""
    starter = make_history_message(f'<@{BOT_ID}> fix the thing', minute=1)
    thread = make_thread(cog.bot, starter=starter)
    asked = {}

    def history(**kwargs):
        asked.update(kwargs)

        async def iterator():
            yield make_history_message('Here is your answer.',
                                       author_id=BOT_ID, minute=3)
        return iterator()

    thread.history = history
    upto = make_history_message('and also fix that', minute=4)

    rebuilt = await cog._thread_history(thread, upto)

    assert asked == {'limit': None, 'oldest_first': True, 'before': upto}
    assert rebuilt.text.split('\n\n')[1:] == [
        f'[2026-08-16 14:01 UTC] Owner:\n<@{BOT_ID}> fix the thing',
        '[2026-08-16 14:03 UTC] You (agent):\nHere is your answer.',
    ]


async def test_a_thread_turn_offers_the_service_its_history(cog, monkeypatch):
    """The service decides whether the history is needed; the cog only
    hands over the way to build it (R2c)."""
    thread = make_thread(cog.bot)
    thread.typing = MagicMock(side_effect=_NoTyping)
    thread.send = AsyncMock()
    make_history(thread, 'Here is your answer.')
    captured = {}

    async def fake_run(key, prompt, on_progress, images=(), reconstruct=None):
        captured['reconstruct'] = reconstruct
        return TaskReport(answer='done', pull_requests=[], timed_out=False)

    monkeypatch.setattr(cog.service, 'run', fake_run)

    await cog._handle_prompt(make_message(thread, 'and also fix that'),
                             thread, 'and also fix that')

    assert captured['reconstruct'] is not None
    rebuilt = await captured['reconstruct']()
    assert 'Here is your answer.' in rebuilt.text


async def test_a_pasted_image_link_is_downloaded_again():
    """A link arrives as an embed rather than an attachment, so there is
    nothing of ours to read it from; it is fetched from the web the way
    the live turn fetches it, so the picture is really in the rebuilt
    history (R2c.2)."""
    async def fetch(url):
        assert url == 'https://example.com/a.png'
        return 'a.png', b'PNG'

    history = await build(
        make_history_message('look at this',
                             embeds=[make_embed('https://example.com/a.png')]),
        fetch=fetch)

    assert history.images == [(f'{MESSAGE_ID}-a.png', b'PNG')]
    assert history.text.splitlines()[-1] == (
        f'[image link https://example.com/a.png: {MESSAGE_ID}-a.png]')


async def test_a_pasted_image_link_that_will_not_come_back_says_why():
    """A message that quietly lost its picture is worse than one that says
    where it was and what happened to it (R2c.2)."""
    history = await build(make_history_message(
        'look at this', embeds=[make_embed('https://example.com/a.png')]))

    assert history.images == []
    assert history.text.splitlines()[-1] == (
        '[image link https://example.com/a.png: could not be fetched '
        '(HTTP 404)]')


class _Response:
    def __init__(self, status=200, content_type='image/png',
                 body=b'PNG', length=None):
        self.status = status
        self.headers = {'Content-Type': content_type}
        if length is not None:
            self.headers['Content-Length'] = str(length)
        self.content = SimpleNamespace(read=AsyncMock(return_value=body))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _session(response):
    return SimpleNamespace(get=lambda url, timeout: response)


async def test_an_image_link_comes_back_as_a_file():
    name, data = await fetch_image_link(
        _session(_Response()), 'https://example.com/pics/a.png?x=1')

    assert (name, data) == ('a.png', b'PNG')


@pytest.mark.parametrize('response, cause', [
    (_Response(status=404), 'HTTP 404'),
    (_Response(content_type='text/html'), 'not an image (text/html)'),
    (_Response(content_type=''), 'not an image (no type)'),
    (_Response(length=IMAGE_LINK_MAX_BYTES + 1),
     f'too large ({IMAGE_LINK_MAX_BYTES + 1} bytes)'),
    (_Response(body=b'x' * (IMAGE_LINK_MAX_BYTES + 1)),
     f'too large (over {IMAGE_LINK_MAX_BYTES} bytes)'),
])
async def test_a_link_that_is_not_an_image_says_why(response, cause):
    """Both the live turn and a rebuilt history quote this reason, so it
    has to name the actual problem (R2c.2)."""
    with pytest.raises(ValueError, match=re.escape(cause)):
        await fetch_image_link(_session(response), 'https://example.com/a')
