"""Local daily vocabulary scores; never stores speech or transcripts."""
from datetime import datetime, timezone
import json
import re
import sqlite3
import random
import time

VOCAB = {
    'punctuation':'Marks that organize writing, such as commas and full stops.',
    'nuance':'A small but meaningful difference.',
    'perspective':'A particular way of seeing something.',
    'articulate':'Express an idea clearly.',
    'deliberate':'Done with thought and intention.',
    'concise':'Clear and brief.',
    'curious':'Interested in learning or finding out more.',
    'relevant':'Connected to the topic being discussed.',
    'insightful':'Showing a useful or deep understanding.',
    'versatile':'Able to adapt to different tasks or situations.',
    'practical':'Useful in real situations.',
    'resilient':'Able to recover from difficulty.',
    'considerate':'Thoughtful about other people.',
    'genuine':'Real or sincere.',
    'clarity':'The quality of being easy to understand.',
    'context':'The surrounding information that helps explain something.',
    'subtle':'Not immediately obvious.',
    'plausible':'Reasonable enough to be believable.',
    'constructive':'Intended to help improve something.',
    'consistent':'Steady or following the same approach.',
    'spontaneous':'Happening without advance planning.',
    'resourceful':'Good at finding ways to solve problems.',
    'tangible':'Real and noticeable.',
    'precise':'Carefully exact.',
    'optimistic':'Expecting a positive outcome.',
    'intriguing':'Interesting in a way that invites curiosity.',
    'collaborate':'Work together on a shared task.',
    'reflect':'Think carefully about an experience or idea.',
    'emphasis':'Special attention or importance.',
    'adaptable':'Able to change to suit new conditions.',
    'momentum':'Progress that helps further progress happen.',
    'coherent':'Connected and easy to follow.',
    'thoughtful':'Showing careful consideration.',
    'balanced':'Taking different sides or needs into account.',
    'candid':'Honest and direct.',
    'distinction':'A difference between things.',
    'initiative':'Taking action without needing to be asked.',
    'reassuring':'Helping someone feel less worried.',
    'credible':'Worthy of belief or trust.',
    'appreciate':'Recognize the value of something.',
}

def tokens(text):
    return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?",text.lower().replace('’',"'"))

class DailyChallenge:
    def __init__(self,path,now=None):
        self.now=now or time.time
        self.db=sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS days(day TEXT PRIMARY KEY, words TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS scores(day TEXT NOT NULL, word TEXT NOT NULL, uses INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(day,word));
          CREATE TABLE IF NOT EXISTS rotation(id INTEGER PRIMARY KEY CHECK(id=1), started REAL NOT NULL, active_key TEXT NOT NULL);
        ''')
        self.day=None; self.tail=[]; self.rollover()

    def rollover(self):
        now=float(self.now())
        state=self.db.execute('SELECT started,active_key FROM rotation WHERE id=1').fetchone()
        if state is None:
            # Existing installations have dates but no creation times. Preserve
            # their latest words/scores and start a full 24-hour period now.
            previous=self.db.execute('SELECT day,words FROM days ORDER BY day DESC LIMIT 1').fetchone()
            day=previous[0] if previous else self.period_key(now)
            words=json.loads(previous[1]) if previous else list(VOCAB)[:10]
            with self.db:
                self.db.execute('INSERT OR IGNORE INTO days VALUES (?,?)',(day,json.dumps(words)))
                self.db.execute('INSERT INTO rotation VALUES (1,?,?)',(now,day))
            state=(now,day)
        started,day=state
        if now>=started+86400:
            previous=json.loads(self.db.execute('SELECT words FROM days WHERE day=?',(day,)).fetchone()[0])
            started+=int((now-started)//86400)*86400
            day=self.period_key(started)
            pool=[w for w in VOCAB if w not in previous]
            words=random.Random(day).sample(pool,10)
            with self.db:
                self.db.execute('INSERT OR IGNORE INTO days VALUES (?,?)',(day,json.dumps(words)))
                self.db.execute('UPDATE rotation SET started=?,active_key=? WHERE id=1',(started,day))
        self.started=started
        if day==self.day:return False
        self.words=json.loads(self.db.execute('SELECT words FROM days WHERE day=?',(day,)).fetchone()[0])
        self.day=day;self.tail=[]
        return True

    @staticmethod
    def period_key(stamp):return datetime.fromtimestamp(stamp,timezone.utc).isoformat(timespec='microseconds')

    @property
    def seconds_remaining(self):return max(0,int(self.started+86400-self.now()))

    def set_words(self,text):
        words=list(dict.fromkeys(' '.join(tokens(w)) for w in text.split(',') if tokens(w)))
        if not 1<=len(words)<=10: raise ValueError('Choose between 1 and 10 words or phrases, separated by commas.')
        if any(len(w)>60 or len(tokens(w))>6 for w in words): raise ValueError('Keep each word or phrase under 60 characters and six words.')
        self.rollover()
        with self.db:
            self.db.execute('UPDATE days SET words=? WHERE day=?',(json.dumps(words),self.day))
        self.words=words; self.tail=[]

    def preview(self,text):
        self.rollover()
        new=tokens(text); combined=self.tail+new; boundary=len(self.tail); hits={}
        for word in self.words:
            phrase=tokens(word)
            count=sum(combined[i:i+len(phrase)]==phrase for i in range(max(0,boundary-len(phrase)+1),len(combined)-len(phrase)+1))
            if count: hits[word]=count
        return hits

    def feed(self,text):
        hits=self.preview(text)
        combined=self.tail+tokens(text)
        if hits:
            with self.db:
                for word,count in hits.items():
                    self.db.execute('INSERT INTO scores VALUES (?,?,?) ON CONFLICT(day,word) DO UPDATE SET uses=uses+excluded.uses',(self.day,word,count))
        n=max((len(tokens(w)) for w in self.words),default=1)-1
        self.tail=combined[-n:] if n else []
        return hits

    def counts(self):
        return dict(self.db.execute('SELECT word,uses FROM scores WHERE day=?',(self.day,)))

    def dates(self):
        return [r[0] for r in self.db.execute('SELECT day FROM days ORDER BY day DESC')]

    def leaderboard(self,day=None):
        if day:
            return list(self.db.execute('SELECT word,uses,day FROM scores WHERE day=? AND uses>0 ORDER BY uses DESC,word',(day,)))
        # Each word's best single day; ties keep the most recent day.
        rows=self.db.execute('SELECT word,uses,day FROM scores WHERE uses>0 ORDER BY uses DESC,day DESC,word').fetchall()
        best={}
        for row in rows: best.setdefault(row[0],row)
        return list(best.values())

    def close(self): self.db.close()
