from pydub import AudioSegment

song = AudioSegment.from_wav('blank_birthday.wav')
name = AudioSegment.from_wav('name.wav')
insert_times = [7.95 * 1000, 12.1 * 1000]

for insert_time in insert_times:
	song = song.overlay(name, position=insert_time)

song.export('birthday.wav')
