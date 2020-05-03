import os
import time
import subprocess

MYCROFT = 1
VOICE   = 2
ESPEAK  = 3


class TTSEngineInterface:
    """This is an abstract interface defining tts engine classes."""

    def get_environment_path(self):
        """Return the command line call to the tts executable."""
        raise NotImplementedError

    def text_to_wav(self, text, filename):
        """Read out the text and save it to a wav file."""
        raise NotImplementedError

    def text_to_wav_slow(self, text):
        raise NotImplementedError

    def text_to_wav_normal(self, text):
        raise NotImplementedError

    def text_to_wav_fast(self, text):
        raise NotImplementedError


class TTSMycroft(TTSEngineInterface):
    """Mycroft engine."""

    def __init__(self, path, voice):
        self.path = path
        self.voice = voice
        self.slow = 2.5
        self.normal = 1
        self.fast = .4

    def get_environment_path(self):
        return self.path

    def text_to_wav_slow(self, text):
        return self.text_to_wav(text, self.slow)

    def text_to_wav_normal(self, text):
        return self.text_to_wav(text, self.normal)

    def text_to_wav_fast(self, text):
        return self.text_to_wav(text, self.fast)

    def text_to_wav(self, text, speed):
        filepath = os.path.join('resources', 'soundclips', 'temp', str(time.time()) + '.wav')
        cmd = (f'{self.path} -t {text} ' +
               f'-voice {self.voice} ' +
               f'--setf duration_stretch={speed} ' +
               f'-o {filepath}')
        print(cmd)
        result = subprocess.run(cmd,
                                shell=True, text=True, capture_output=True, check=True)
        if not result.returncode == 0:
            print('Something went wrong saving mycroft mimic to file.')
        else:
            return filepath


class TTSVoice(TTSEngineInterface):
    """Voice.exe engine."""

    def __init__(self, path):
        self.path = path

    def get_environment_path(self):
        return self.path


class TTSEspeak(TTSEngineInterface):
    """Espeak engine."""

    def __init__(self):
        self.path = 'espeak'
        # fast: 350
        # normal: no arg?
        # slow: 50
        pass

    def get_environment_path(self):
        return self.path


def construct(engine: int, path=None, voice=None):
    """Construct and return a TTSEngine object."""
    if engine == MYCROFT:
        if not path:
            raise RuntimeError('No path specified for mycroft tts engine')
        out = TTSMycroft(path=path, voice=voice)
    elif engine == VOICE:
        if not path:
            raise RuntimeError('No path specified for voice.exe tts engine')
        out = TTSVoice(path=path)
    elif engine == ESPEAK:
        out = TTSEspeak()
    else:
        raise RuntimeError('Invalid tts engine specified.')

    return out
