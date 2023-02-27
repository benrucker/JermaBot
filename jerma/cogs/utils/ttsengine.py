import os
import subprocess
import time

from pydub.audio_segment import AudioSegment

MYCROFT    = 1
VOICE      = 2
ESPEAK     = 3
OPEN_JTALK = 4


class TTSEngine:
    """This is an abstract interface defining tts engine classes."""

    def get_environment_path(self):
        """Return the command line call to the tts executable."""
        raise NotImplementedError

    def text_to_wav(self, text: str, filename: str):
        """Read out the text and save it to a wav file."""
        raise NotImplementedError

    def text_to_wav_slow(self, text: str):
        raise NotImplementedError

    def text_to_wav_normal(self, text: str):
        raise NotImplementedError

    def text_to_wav_fast(self, text: str):
        raise NotImplementedError


class TTSMycroft(TTSEngine):
    """Mycroft engine."""

    def __init__(self, path, voice):
        self.path = path
        self.voice = voice
        self.slow = 2.5
        self.normal = 1
        self.fast = .5
        self.vol_raise_amount = 9

    def get_environment_path(self):
        return self.path

    def text_to_wav_slow(self, text):
        return self.text_to_wav(text, self.slow)

    def text_to_wav_normal(self, text):
        return self.text_to_wav(text, self.normal)

    def text_to_wav_fast(self, text):
        return self.text_to_wav(text, self.fast)

    def raise_volume(self, file):
        sound = AudioSegment.from_file(file, format="wav") + self.vol_raise_amount
        sound.export(file)

    def text_to_wav(self, text, speed):
        filepath = os.path.join('resources', 'soundclips', 'temp', str(time.time()) + '.wav')
        cmd = (f'{self.path} -t "{text}" ' +
               f'-voice {self.voice} ' +
               f'--setf duration_stretch={speed} ' +
               f'-o {filepath}')
        print(cmd)
        result = subprocess.run(cmd,
                                shell=True, text=True, capture_output=True, check=True)
        if not result.returncode == 0:
            print('Something went wrong saving mycroft mimic to file.')
        else:
            self.raise_volume(filepath)
            return filepath


class TTSOpenJtalk(TTSEngine):

    def __init__(self, path, voice, dic):
        self.path = path
        self.voice = voice
        self.dic = dic
        # self.slow = 2.5
        # self.normal = 1
        # self.fast = .5
        self.vol_raise_amount = 13

    def get_environment_path(self):
        """Return the command line call to the tts executable."""
        return self.path

    def text_to_wav(self, text):
        """Read out the text and save it to a wav file."""
        filepath = os.path.join('resources', 'soundclips', 'temp', str(time.time()) + '.wav')
        text = text.replace('"', '')
        cmd = (f'echo "{text}" | ' +
               f'{self.path} ' +
               f'-x {self.dic} '
               f'-m {self.voice} ' +
               #f'--setf duration_stretch={speed} ' +
               f'-ow {filepath}')
        print(cmd)
        result = subprocess.run(cmd,
                                shell=True, text=True, capture_output=True)
        if not result.returncode == 0:
            print('Something went wrong saving open_jtalk mimic to file.')
            print(result.stdout)
            print(result.stderr)
        else:
            self.raise_volume(filepath)
            return filepath

    def text_to_wav_normal(self, text):
        # return self.text_to_wav(text, self.normal)
        return self.text_to_wav(text)

    def raise_volume(self, file):
        sound = AudioSegment.from_file(file, format="wav") + self.vol_raise_amount
        sound.export(file)


class TTSVoice(TTSEngine):
    """Voice.exe engine."""

    def __init__(self, path):
        self.path = path

    def get_environment_path(self):
        return self.path


class TTSEspeak(TTSEngine):
    """Espeak engine."""

    def __init__(self):
        self.path = 'espeak'
        # fast: 350
        # normal: no arg?
        # slow: 50
        pass

    def get_environment_path(self):
        return self.path


def construct(engine: int, path=None, voice=None, **kwargs):
    """Construct and return a TTSEngine object."""
    if engine == MYCROFT:
        if not path:
            raise RuntimeError('No path specified for mycroft tts engine')
        return TTSMycroft(path=path, voice=voice)
    elif engine == VOICE:
        if not path:
            raise RuntimeError('No path specified for voice.exe tts engine')
        return TTSVoice(path=path)
    elif engine == ESPEAK:
        return TTSEspeak()
    elif engine == OPEN_JTALK:
        return TTSOpenJtalk(path='open_jtalk', voice=voice, **kwargs)
    else:
        raise RuntimeError('Invalid tts engine specified.')
