from pathlib import Path
from datetime import datetime,timezone
import tempfile,sqlite3,json
from daily_challenge import DailyChallenge

with tempfile.TemporaryDirectory() as folder:
    path=Path(folder)/'scores.sqlite3'
    clock=[datetime(2026,9,27,23,30,tzinfo=timezone.utc).timestamp()]
    d=DailyChallenge(path,lambda:clock[0]); first=d.day; start=d.started
    assert len(d.words)==10 and 'punctuation' in d.words
    d.feed('Punctuation, punctuation! Unpunctuated writing.')
    assert d.counts()['punctuation']==2
    d.set_words('punctuation, you know, perspective')
    d.feed('You');d.feed('know how punctuation works.')
    assert d.counts()['you know']==1 and d.counts()['punctuation']==3
    assert d.preview('punctuation punctuation')['punctuation']==2
    assert d.counts()['punctuation']==3
    clock[0]=start+3600
    assert not d.rollover() and d.day==first  # midnight is irrelevant
    assert d.seconds_remaining==82800
    d.close();d=DailyChallenge(path,lambda:clock[0])
    assert d.started==start and d.counts()['punctuation']==3
    d.set_words('punctuation, perspective')
    assert d.started==start  # editing never resets the timer
    clock[0]=start+86399;assert not d.rollover()
    old=set(d.words)
    clock[0]=start+86400;assert d.rollover()
    assert d.counts()=={} and len(d.words)==10 and not old.intersection(d.words)
    assert d.leaderboard(first)[0][1]==3
    second=d.day;old=set(d.words);d.close()
    clock[0]=start+4*86400+120
    d=DailyChallenge(path,lambda:clock[0])
    assert d.day!=second and d.seconds_remaining==86280
    assert len(d.words)==10 and not old.intersection(d.words)
    current=d.day;clock[0]-=3600;assert not d.rollover() and d.day==current
    d.close()
    legacy=Path(folder)/'legacy.sqlite3';c=sqlite3.connect(legacy)
    c.executescript('CREATE TABLE days(day TEXT PRIMARY KEY,words TEXT NOT NULL); CREATE TABLE scores(day TEXT,word TEXT,uses INTEGER,PRIMARY KEY(day,word));')
    c.execute('INSERT INTO days VALUES (?,?)',('2026-09-27',json.dumps(['punctuation'])))
    c.execute('INSERT INTO scores VALUES (?,?,?)',('2026-09-27','punctuation',11));c.commit();c.close()
    d=DailyChallenge(legacy,lambda:clock[0])
    assert d.words==['punctuation'] and d.counts()['punctuation']==11 and d.seconds_remaining==86400
    d.close()
print('PASS: exact 24h boundary, midnight, restart, downtime catch-up, fresh words, edits, clock rollback, legacy score migration and counting')
