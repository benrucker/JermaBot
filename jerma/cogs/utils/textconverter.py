# import eng_to_ipa as ipa
# import epitran
import os
import regex as re
import subprocess
from typing import List, Optional

jpattern = re.compile(r'([\p{IsHan}\p{IsBopo}\p{IsHira}\p{IsKatakana}]+)', re.UNICODE)

def has_no_english(text: str) -> bool:
    return len(text) == 0 or re.match(r'\W', text)

def eng_to_katakana(eng_text: str) -> Optional[str]:
    if has_no_english(eng_text): return eng_text
    print('Converting:', eng_text)
    # ipa_text = ipa.convert(eng_text)
    # ipa_text = epitran.transliterate(eng_text)
    # print(ipa_text)
    env = os.environ.copy()
    env['KANA_TYPE'] = 'katakana'
    # cmd = f'python lexconvert.py --phones kana-approx {ipa_text}'
    path = os.path.join('cogs','utils','lexconvert.py')
    cmd = f'python {path} --phones kana-approx {eng_text}'
    # cmd = f'cd'
    result = subprocess.run(cmd,
                            shell=True, capture_output=True)
    if result.returncode != 0:
        print('Lexconvert returned result code:', result.returncode)
        print(result.stdout.decode('utf-8'))
        print(result.stderr.decode('utf-8'))
        return None
    katakana = result.stdout.decode('utf-8').strip()
    print('Converted to:', katakana)
    return katakana

def mixed_to_katakana(text: List[str]) -> List[str]:
    out = []
    for word in text:
        match = jpattern.match(word)
        if match:
            print(match)
            out.append(word)
        else:
            kana = eng_to_katakana(word)
            if kana: out.append(kana)
    return out

def split(text: str):
    """Split sentences, retain punctuation as own entry."""
    return re.split(r'(\W+)', text)

if __name__ == '__main__':
    # arg = 'penis, titties!  anime tiddies. 私ワリンゴです。'.split(' ')
    arg = re.split(r'(\W+)', 'penis, titties! anime tiddies. 私ワリンゴです。')
    print(arg)
    # eng_to_katakana('penis titties anime tiddies. 私ワリンゴです。')

    kana = mixed_to_katakana(arg)
    print(kana)
    kana_joined = ''.join(kana)
    print(kana_joined)

