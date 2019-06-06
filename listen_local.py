import sys
import os
from pocketsphinx.pocketsphinx import *
from sphinxbase.sphinxbase import *
import pyaudio

modeldir = "E:\\Documents\\Discord\\discord-jerma\\pocketsphinx\\model"
datadir = "E:\\Documents\\Discord\\discord-jerma\\pocketsphinx\\test\\data"


class LocalDecoder():
    """Search for a keyword."""
    def __init__(self, keyword='forward'):
        """Create a decoder with certain model."""
        self.config = Decoder.default_config()
        self.config.set_string('-hmm', os.path.join(modeldir, 'en-us\\en-us'))
        self.config.set_string('-dict', os.path.join(modeldir, 'en-us\\cmudict-en-us.dict'))
        self.config.set_string('-keyphrase', keyword)
        #self.config.set_float('-vad_threshold', .5)
        #self.config.set_float('-samprate', 48000)
        #self.config.set_float('-nfft', 2048.0)
        #self.config.set_string('-remove_noise', 'no')
        #self.config.set_string('buffer_size', 3840)
        self.config.set_float('-kws_threshold', 1e-20)  # 1e-5 to 1e-50 (says minus on docs)


    def start_streams(self, stream=None):
        print('startin streams')
        self.decoder = Decoder(self.config)
        self.stream = stream
        self.decoder.start_utt()

    async def listen_for_keyword_loop(self, ctx, after=None):
        #p = pyaudio.PyAudio()
        #stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=1024)
        #stream = _stream
        #stream.start_stream()
        # Process audio chunk by chunk. On keyword detected perform action and restart search
        self.decoder.start_utt()
        while True:
            buf = self.stream.read(1024)
            #print(type(buf))
            #buf = data.data
            if buf:
                #print(type(buf), len(buf))
                self.decoder.process_raw(buf, False, False)
            else:
                pass
            if self.decoder.hyp() is not None:
                #print([(seg.word, seg.prob, seg.start_frame, seg.end_frame) for seg in decoder.seg()])
                print("Detected keyword, returning.")
                self.decoder.end_utt()
                self.decoder.start_utt()
                after('keyword_heard', ctx)


    def listen_for_keyword(self, data):
        #p = pyaudio.PyAudio()
        #stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=1024)
        #stream = _stream
        #stream.start_stream()
        # Process audio chunk by chunk. On keyword detected perform action and restart search
        #decoder.start_utt()
        #while True:
        #buf = self.stream.read(1024)
        #print(type(data.data), type(data.packet.decrypted_data))
        buf = data
        if buf:
            #print(type(buf), len(buf))
            self.decoder.process_raw(buf, False, False)
        else:
            return False
        if self.decoder.hyp() is not None:
            #print([(seg.word, seg.prob, seg.start_frame, seg.end_frame) for seg in decoder.seg()])
            print("Detected keyword, returning.")
            self.decoder.end_utt()
            return True
            #decoder.start_utt()
        return False
