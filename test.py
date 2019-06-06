"""# here we go

import speech_recognition as sr

r = sr.Recognizer()

with sr.Microphone() as source:
    print('Say Something!')
    audio = r.listen(source)
    print('Done!')

text = r.recognize_google(audio)
print(text)
"""

import sys, os
from pocketsphinx.pocketsphinx import *
from sphinxbase.sphinxbase import *
import pyaudio

modeldir = "E:\\Documents\\Discord\\discord-listen\\pocketsphinx\\model"
datadir = "E:\\Documents\\Discord\\discord-listen\\pocketsphinx\\test\\data"

# Create a decoder with certain model
config = Decoder.default_config()
config.set_string('-hmm', os.path.join(modeldir, 'en-us\\en-us'))
config.set_string('-dict', os.path.join(modeldir, 'en-us\\cmudict-en-us.dict'))
config.set_string('-keyphrase', 'forward')
config.set_float('-kws_threshold', 1e+20)

async def listen_for_keyword(AudioStream):
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=1024)
    stream.start_stream()
    # Process audio chunk by chunk. On keyword detected perform action and restart search
    decoder = Decoder(config)
    decoder.start_utt()
    while True:
        buf = stream.read(1024)
        if buf:
            decoder.process_raw(buf, False, False)
        else:
            break
        if decoder.hyp() is not None:
            #print([(seg.word, seg.prob, seg.start_frame, seg.end_frame) for seg in decoder.seg()])
            print("Detected keyword, returning.")
            decoder.end_utt()
            #decoder.start_utt()
