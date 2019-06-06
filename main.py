import sys
import time
import pyaudio
import wave
import discord
import audioop
import random
import colorama
from colorama import Fore as t
from glob import glob
from discord.ext import commands
from discord.opus import Decoder
import speech_recognition as sr
from listen_local import LocalDecoder
from pocketsphinx import LiveSpeech
import subprocess
from pydub import AudioSegment

# TODO:
#  - say something upon join
#  - add thanos snap 'ooo up in smoke'

colorama.init(autoreset=True)

tts_path = 'C:\\Program Files (x86)\\eSpeak\\command_line\\voice.exe'

prefix = '$'
bot = commands.Bot(prefix)
r = sr.Recognizer()


@bot.event
async def on_message(message):
    if message.content.startswith('$$'):
        return # protection against hackerbot commands

    if message.content.startswith(prefix):
        print(f'{message.author.name} - {message.guild} #{message.channel}: {t.CYAN}{message.content}')
    elif message.author == message.guild.me:
        print(f'{message.author.name} - {message.guild} #{message.channel}: {message.content}')
    try:
        await bot.process_commands(message)
    except JermaException as e:
        print('Caught JermaException: ' + str(e))
    #except Exception as e:
    #    print(e)


@bot.command()
async def join(ctx):
    vc = await connect_to_user(ctx)


@bot.command()
async def leave(ctx):
    await ctx.guild.voice_client.disconnect()


@bot.command()
async def perish(ctx):
    await bot.close()


@bot.command()
async def sendfile(ctx):
    await ctx.send(file=discord.File('test.png'))


@bot.command()
async def listen(ctx):
    vc = await connect_to_user(ctx)
    do_listen(ctx, vc)


@bot.command()
async def testafter(ctx):
    print('testafter command entered')
    testfunc(ctx, after=bot.dispatch)

@bot.command()
async def testsnap(ctx):
    print('testjermasnap')
    if not is_major(ctx.author):
        return

    vc = await connect_to_user(ctx)
    soul_stone = get_soul_stone_channel(ctx)

    users = ctx.author.voice.channel.members
    users.remove(ctx.me)
    snapees = random.sample(users, k=len(users) // 2)

    vc = await connect_to_user(ctx)
    soul_stone = get_soul_stone_channel(ctx)

    sound, delay, length = get_snap_sound()
    vc.play(discord.FFmpegPCMAudio(sound))
    time.sleep(delay)

    #for snapee in snapees:
    #    await snapee.move_to(soul_stone)

    time.sleep(length - delay)
    do_moonlight = random.random() < 0.25
    if do_moonlight:
        ctx.guild.voice_client.move_to(soul_stone)
        vc.play(discord.FFmpegPCMAudio('soundclips\\moonlight.wav'))
    else:
        vc.play(discord.FFmpegPCMAudio('soundclips\\snaps\\up in smoke.mp3'))


@bot.command()
async def jermasnap(ctx):
    print('jermasnap')
    if not is_major(ctx.author):
        return

    vc = await connect_to_user(ctx)
    soul_stone = get_soul_stone_channel(ctx)

    users = ctx.author.voice.channel.members
    users.remove(ctx.me)
    snapees = random.sample(users, k=len(users) // 2)

    vc = await connect_to_user(ctx)
    soul_stone = get_soul_stone_channel(ctx)

    sound, delay, length = get_snap_sound()
    vc.play(discord.FFmpegPCMAudio(sound))
    time.sleep(delay)

    for snapee in snapees:
        await snapee.move_to(soul_stone)

    time.sleep(length - delay)
    do_moonlight = random.random() < 0.25
    if do_moonlight:
        await ctx.guild.voice_client.move_to(soul_stone)
        vc.play(discord.FFmpegPCMAudio('soundclips\\moonlight.wav'))
    else:
        vc.play(discord.FFmpegPCMAudio('soundclips\\snaps\\up in smoke.mp3'))


@bot.command()
async def jermalofi(ctx):
    print('jermalofi')
    vc = await connect_to_user(ctx)
    vc.play(LoopingSource('soundclips\\birthdayloop.wav', loop_factory))


@bot.command()
async def keyword(ctx):
    vc = await connect_to_user(ctx)
    do_listen_keyword(ctx, vc)


@bot.command()
async def passthrough(ctx):
    vc = await connect_to_user(ctx)
    user = ctx.author

    CHUNK = 1024

    #wf = wave.open('test.wav', 'rb')

    sink = discord.AudioSink()
    user_sink = discord.UserFilter(sink, ctx.message.author)
    # sink = StreamSink(user_sink, ctx)

    # p = pyaudio.PyAudio()
    # #
    # stream = p.open(format=p.get_format_from_width(Decoder.SAMPLE_SIZE//Decoder.CHANNELS),
    #                 channels=Decoder.CHANNELS,
    #                 #channels=1,
    #                 #rate=Decoder.SAMPLING_RATE,
    #                 rate=16000,
    #                 output=True)

    # print('format =', p.get_format_from_width(Decoder.SAMPLE_SIZE//Decoder.CHANNELS))
    # print('channels =', Decoder.CHANNELS)
    # print('rate =', Decoder.SAMPLING_RATE)

    sink = StreamSink(user_sink, ctx, user, vc)

    vc.listen(sink)

    # vc.play(discord.PCMAudio(stream))
    # vc.play(discord.FFmpegPCMAudio('test.wav'))
    #
    # data = sink.read(size=CHUNK)
    # while True:
    #     if data:
    #         print(data)
    #         stream.write(data)
    #     data = sink.read(size=CHUNK)
    #
    # stream.stop_stream()
    # stream.close()
    #
    # p.terminate()

@bot.command()
async def birthday(ctx, *args):
    if not args:
        raise discord.InvalidArgument
    to_speak = ' '.join(args)
    vc = await connect_to_user(ctx)
    vc.play(discord.FFmpegPCMAudio(birthday_wave(
                                     text_to_wav(to_speak, ctx, 'birthday_name'), ctx)))


@bot.command()
async def speakfile(ctx, *args):
    if not args:
        raise discord.InvalidArgument
    to_speak = ' '.join(args)
    await ctx.send(file=discord.File(text_to_wav(to_speak, ctx, 'speakfile', speed=0)))

@bot.command()
async def adderall(ctx, *args):
    if not args:
        raise discord.InvalidArgument
    to_speak = ' '.join(args)
    vc = await connect_to_user(ctx)
    vc.play(discord.FFmpegPCMAudio(text_to_wav(to_speak, ctx, 'adderall', speed=7)))

@bot.command()
async def speak(ctx, *args):
    if not args:
        raise discord.InvalidArgument
    to_speak = ' '.join(args)
    vc = await connect_to_user(ctx)
    vc.play(discord.FFmpegPCMAudio(text_to_wav(to_speak, ctx, 'speak')))

@bot.command()
async def speakdrunk(ctx, *args):
    if not args:
        raise discord.InvalidArgument
    to_speak = ''.join(args)
    vc = await connect_to_user(ctx)
    vc.play(discord.FFmpegPCMAudio(text_to_wav(to_speak, ctx, 'speakdrunk', speed=-10)))


@bot.command()
async def loopaudio(ctx, *args):
    vc = await connect_to_user(ctx)
    vc.play(LoopingSource(args, loop_factory))

def loop_factory(filename):
    return discord.FFmpegPCMAudio(filename)

def testfunc(ctx, after=None):
    print('testfunc entered', type(after))
    time.sleep(3)
    after('testevent', ctx)

def get_soul_stone_channel(ctx):
    for channel in ctx.guild.voice_channels:
        if channel.id == 343939767068655616:
            return channel
    raise BaseException('channel not found')

def is_major(member):
    for role in member.roles:
        if role.id == 374095810868019200:
            return True
    return False

def get_snap_sound():
    sounds = []
    with open('soundclips\\snaps\\sounds.txt', 'r', encoding='utf-8') as file:
        for sound in file.read().split('\n'):
            sounds.append(sound.split(' '))
    print(sounds)
    choice = random.choice(sounds)
    choice[0] = 'soundclips\\snaps\\' + choice[0]
    choice[1] = float(choice[1])
    choice[2] = float(choice[2])
    return choice

@bot.event
async def on_testevent(ctx):
    print('testevent event triggered')
    await ctx.send('testevent triggered')


@bot.event
async def on_start_listen(ctx, listener):
    while True:
        print('starting to listen')
        await listener.listen_for_keyword_loop(ctx, after=bot.dispatch)


@bot.event
async def on_finished_listen(ctx):
    print('on_finished_listen')
    #await ctx.disconnect()
    await ctx.send(file=discord.File('soundclips\\temp\\test.wav'))


@bot.event
async def on_keyword_heard(ctx):
    await ctx.send('keyword detected')


@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Activity(name='rats lofi.',
                                                        type=discord.ActivityType(2)))
    print("Let's fucking go, bois.")

def text_to_wav(text, ctx, label, speed=0):
    soundclip = generate_id_path(label, ctx)
    file = 'E:\\Documents\\Discord\\discord-jerma\\' + soundclip
    subprocess.call([tts_path, '-r', str(speed), '-o', file, text])
    return soundclip


def generate_id_path(label, ctx):
    return 'soundclips\\temp\\' + label + str(ctx.guild.id) + '.wav'


async def connect_to_user(ctx):
    try:
        vc = ctx.guild.voice_client
        user_channel = ctx.author.voice.channel
        if not vc:
            vc = await ctx.author.voice.channel.connect()
        elif not vc.channel == user_channel:
            await ctx.guild.voice_client.move_to(user_channel)
        return vc
    except:
        #print('Caught ' + type(e) + ':', e.message)
        #print('User was probably not in a channel or something.')
        await ctx.send("Hey gamer, you're not in a voice channel. Totally uncool.")
        raise JermaException("User was not in a voice channel or something.")


def do_listen(ctx, vc):
    """Listen to users and execute commands as needed."""
    print('do_listen')
    sink = discord.WaveSink('soundclips\\temp\\test.wav')
    user_sink = discord.UserFilter(sink, ctx.message.author)
    silence_sink = CustomTimedFilter(user_sink, 3)
    vc.listen(silence_sink, ctx)
    #sink.cleanup()
    #print('done listening')
    #await ctx.send(file='test.wav')


def do_listen_keyword(ctx, vc):
    """Listen to users and execute commands as needed."""
    print('do_listen_keyword')
    sink = discord.AudioSink()
    user_sink = discord.UserFilter(sink, ctx.message.author)
    keyword_sink = KeywordFilter(user_sink, ctx, ctx.message.author, vc)
    vc.listen(keyword_sink)


def birthday_wave(name, ctx):
    song = AudioSegment.from_wav('soundclips\\blank_birthday.wav')
    name = AudioSegment.from_wav(name)
    insert_times = [7.95 * 1000, 12.1 * 1000]

    for insert_time in insert_times:
    	song = song.overlay(name, position=insert_time)

    outpath = generate_id_path('birthday_name', ctx)
    song.export(outpath)
    return outpath


class WaveSink(discord.AudioSink):
    def __init__(self, destination):
        self._file = wave.open(destination, 'wb')
        self._file.setnchannels(Decoder.CHANNELS)
        self._file.setsampwidth(Decoder.SAMPLE_SIZE//Decoder.CHANNELS)
        self._file.setframerate(Decoder.SAMPLING_RATE)
        #self._file.setframerate(16000)

    def write(self, data):
        self._file.writeframes(data.data)

    def cleanup(self):
        try:
            self._file.close()
        except:
            pass


class CustomTimedFilter(discord.ConditionalFilter):
    def __init__(self, destination, duration, *, start_on_init=False):
        super().__init__(destination, self._predicate)
        self.duration = duration
        if start_on_init:
            self.start_time = self.get_time()
        else:
            self.start_time = None
            self.write = self._write_once

    def _write_continue(self, data):
        if self.predicate(data):
            self.destination.write(data)
        else:
            print('done listening in TimedFilter')
            raise SinkExit('finished_listen')

    def _write_once(self, data):
        """Begins the writing process."""
        self.start_time = self.get_time()
        self._write_continue(data)
        self.write = self._write_continue

    def _predicate(self, data):
        return self.start_time and self.get_time() - self.start_time < self.duration

    def get_time(self):
        return time.time()


class KeywordFilter(discord.AudioSink):
    """Fires event `keyword` upon hearing the keyword.

    Sends audio data to sphinx listener.
    """

    def __init__(self, sink, ctx, user, vc):
        #super().__init__(destination, self._predicate)
        self.keyword_listener = LocalDecoder()
        self.keyword_listener.start_streams(stream=self)
        self.ctx = ctx
        self.user = user
        self.sink = bytearray()
        self.conversion_state = None
        self.conversion_state2 = None
        self.vc = vc
        #bot.dispatch('start_listen', ctx, self.keyword_listener)

    def write(self, data):
        if data.user == self.user:
            #result = self.keyword_listener.listen_for_keyword(data.data)
            #self.vc.send_audio_packet(data.data)
            #print(type(data.data))
            #data = bytes([y for (x,y) in enumerate(data.data) if x % 2 == 0])
            ndata = []
            for i in range(len(data.data)):
                if i + 1 % 4:
                    ndata.extend(data.data[i]).extend(data.data[i+1])
            data, self.conversion_state = audioop.ratecv(bytes(ndata),
                                                        Decoder.SAMPLE_SIZE//Decoder.CHANNELS,
                                                        1,
                                                        Decoder.SAMPLING_RATE,
                                                        16000,
                                                        self.conversion_state)
            #self.sink.extend(data)
            #print(data)
            #result = self.keyword_listener.listen_for_keyword(data.data)
            result = self.keyword_listener.listen_for_keyword(data)

            data, self.conversion_state2 = audioop.ratecv(data,
                                                        Decoder.SAMPLE_SIZE//Decoder.CHANNELS,
                                                        1,
                                                        16000,
                                                        48000,
                                                        self.conversion_state2)

            self.vc.send_audio_packet(data)
            if result:
                raise SinkExit('keyword')
                bot.dispatch('keyword', self.ctx)

    def read(self, size=1024):
        """Return data array of size size if enough data has been written."""
        if len(self.sink) < 1024:
            return None
        out = self.sink[:size]
        self.sink = self.sink[size:]
        #print(len(out))
        return bytes(out)

class StreamSink(discord.AudioSink):


    def __init__(self, _sink, ctx, user, vc):
        #super().__init__(destination, self._predicate)
        #self.keyword_listener = LocalDecoder()
        #self.keyword_listener.start_streams(stream=self)
        self.ctx = ctx
        self.sink = bytearray()
        #self.stream = stream
        self.user = user
        self.vc = vc
        self.stop = False
        self.other_sink = _sink
        #bot.dispatch('start_listen', ctx, self.keyword_listener)


    def write(self, data):
        if data.user == self.user and not self.stop:
            #self.vc.send_audio_packet(data.data)
            #print(type(data.data))
            #for (x,y) in enumerate(data.data):
            #    print (x, y)
            #new_data = bytes([y for (x,y) in enumerate(data.data) if x % 3 == 0])
            #self.sink.extend(new_data)
            #_sink.write(data)
            #print(len(data.data), len(new_data))
            #self.stream.write(data.data)
            self.vc.send_audio_packet(data.data)
            #self.vc.send_audio_packet(self.testmorph(data.data))
            #stop = self.keyword_listener.listen_for_keyword(data.data)

    def testmorph(self, data):
        #return bytes([data[x * 2 % len(data)] for x in range(len(data))])
        return bytes([data[x] for x in range(len(data))])

    def read(self, size=1024):
        """Return data array of size size if enough data has been written."""
        if len(self.sink) < 1024:
            return None
        out = self.sink[:size]
        self.sink = self.sink[size:]
        #print(len(out))
        return bytes(out)


class CustomVoiceClient(discord.VoiceClient):
    def __init__(self, vc):
        self.super = vc
        self.__dict__ = vc.__dict__.copy()  # does this actually work?

    def listen(self, sink, ctx):
        """Receives audio into a :class:`AudioSink`."""
        if not self.is_connected():
            raise discord.ClientException('Not connected to voice.')

        if not isinstance(sink, discord.AudioSink):
            raise TypeError('sink must be an AudioSink not {0.__class__.__name__}'.format(sink))

        if self.is_listening():
            raise discord.ClientException('Already receiving audio.')

        self._reader = CustomAudioReader(sink, self, after=bot.dispatch)
        setattr(self._reader, 'ctx', ctx)
        self._reader.start()


class CustomAudioReader(discord.reader.AudioReader):
    # def _do_run(self):
    #     while not self._end.is_set():
    #         if not self._connected.is_set():
    #             self._connected.wait()
    #
    #         ready, _, err = discord.select.select([self.client.socket], [],
    #                                               [self.client.socket], 0.01)
    #         if not ready:
    #             if err:
    #                 print("Socket error")
    #             continue
    #
    #         try:
    #             raw_data = self.client.socket.recv(4096)
    #         except discord.socket.error as e:
    #             t0 = time.time()
    #
    #             if e.errno == 10038: # ENOTSOCK
    #                 continue
    #
    #             discord.log.exception("Socket error in reader thread ")
    #             print(f"Socket error in reader thread: {e} {t0}")
    #
    #             with self.client._connecting:
    #                 timed_out = self.client._connecting.wait(20)
    #
    #             if not timed_out:
    #                 raise
    #             elif self.client.is_connected():
    #                 print(f"Reconnected in {time.time()-t0:.4f}s")
    #                 continue
    #             else:
    #                 raise
    #
    #         try:
    #             packet = None
    #             if not discord.rtp.is_rtcp(raw_data):
    #                 packet = discord.rtp.decode(raw_data)
    #                 packet.decrypted_data = self._decrypt_rtp(packet)
    #             else:
    #                 packet = discord.rtp.decode(self._decrypt_rtcp(raw_data))
    #                 if not isinstance(packet, discord.rtp.ReceiverReportPacket):
    #                     print(packet)
    #
    #                     # TODO: Fabricate and send SenderReports and see what happens
    #
    #                 for buff in list(self._buffers.values()):
    #                     buff.feed_rtcp(packet)
    #
    #                 continue
    #
    #         except discord.CryptoError:
    #             discord.log.exception("CryptoError decoding packet %s", packet)
    #             continue
    #
    #         except Exception:
    #             discord.log.exception("Error unpacking packet")
    #             discord.traceback.print_exc()
    #
    #         else:
    #             if packet.ssrc not in self.client._ssrcs:
    #                 discord.log.debug("Received packet for unknown ssrc %s", packet.ssrc)
    #
    #
    #             self._buffers[packet.ssrc].feed_rtp(packet)
    def _write_to_sink(self, pcm, opus, packet):
        try:
            data = opus if self.sink.wants_opus() else pcm
            user = self._get_user(packet)
            self.sink.write(discord.reader.VoiceData(data, user, packet))
        except SinkExit as e:
            print('received sinkexit', e)
            self.exit = e.message
            #discord.log.info("Shutting down reader thread %s", self)
            self.stop()
            self._stop_decoders(**e.kwargs)
        except Exception as e:
            print('_write_to_sink():', e)
            #discord.traceback.print_exc()
            # insert optional error handling here

    def run(self):
        try:
            print('try clause in reader.run')
            self._do_run()
        #except SinkExit as exit:
        #    print('received sinkexit')
        #    self.exit = exit.message
        except discord.socket.error as exc:
            print('discord socket error')
            self._current_error = exc
            self.stop()
        except Exception as exc:
            print('!!! general exception', exc)
            #discord.traceback.print_exc()
            self._current_error = exc.message
            self.stop()
        finally:
            print('finally clause in reader.run')
            self._stop_decoders()
            try:
                self.sink.cleanup()
            except Exception as e:
                print("Error during sink cleanup")
                print(e)
                # Testing only
                #traceback.print_exc()

            self._call_after()

    def _call_after(self):
        if self.after is not None:
            try:
                print('trying to call after')
                self.after(self.exit, self.ctx)
            except Exception as e:
                print(e)
                #log.exception('Calling the after function failed.')


class LoopingSource(discord.AudioSource):
    def __init__(self, param, source_factory):
        self.factory = source_factory
        self.param = param
        self.source = source_factory(self.param)

    def read(self):
        ret = self.source.read()
        if not ret:
            self.source.cleanup()
            self.source = self.factory(self.param)
            ret = self.source.read()
        return ret


class SinkExit(discord.SinkExit):
    def __init__(self, *args, drain=True, flush=False, **kwargs):
        self.message = args[0]
        self.kwargs = kwargs


class JermaException(BaseException):
    pass

def get_amplitude():
    return 10


bot.run('NTc5NDQ1ODMzOTM4NzYzODE2.XOCRFw.nRRo7WhDAPHYjtffNkHyFrH7zpY')
