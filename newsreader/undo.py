"""Ten-minute deletion receipts, preserving a word's media and SRS schedule."""
import base64
import json
import uuid
from datetime import datetime, timedelta
from flask import Blueprint, abort, jsonify
from .db import get_db, now

undo=Blueprint('undo',__name__,url_prefix='/api/undo')
CHILDREN=('word_meta','word_contexts','word_senses','word_notes','word_aliases','word_cards','word_memberships','srs_cards')


def pack_row(row):
    return {key: {'bytes':base64.b64encode(value).decode()} if isinstance(value,bytes) else value for key,value in dict(row).items()}


def delete_word(db,identity):
    db.execute('BEGIN IMMEDIATE')
    word=db.execute('SELECT * FROM vocabulary WHERE id=?',(identity,)).fetchone()
    if not word:abort(404)
    snapshot={'vocabulary':[pack_row(word)]}
    for table in CHILDREN:snapshot[table]=[pack_row(r) for r in db.execute('SELECT * FROM '+table+' WHERE word_id=?',(identity,))]
    snapshot['events']=[r[0] for r in db.execute('SELECT id FROM srs_events WHERE word_id=?',(identity,))]
    snapshot['expires']=(datetime.fromisoformat(now())+timedelta(minutes=10)).isoformat()
    token=uuid.uuid4().hex
    # Expired receipts are discarded during the next deletion; never on a timer
    # while another request could still be restoring them.
    for row in db.execute("SELECT id,payload_json FROM extension_records WHERE kind='undo-word'").fetchall():
        if datetime.fromisoformat(json.loads(row['payload_json'])['expires'])<datetime.fromisoformat(now()):
            db.execute('DELETE FROM extension_records WHERE id=?',(row['id'],))
    db.execute('INSERT INTO extension_records VALUES(?,?,?,1,?,?)',(token,'undo-word',json.dumps(snapshot),now(),now()))
    db.execute('DELETE FROM vocabulary WHERE id=?',(identity,));db.commit()
    return token


@undo.post('/<token>')
def restore(token):
    db=get_db();db.execute('BEGIN IMMEDIATE')
    row=db.execute("SELECT * FROM extension_records WHERE id=? AND kind='undo-word'",(token,)).fetchone()
    if not row:abort(404)
    snapshot=json.loads(row['payload_json'])
    if datetime.fromisoformat(snapshot['expires'])<datetime.fromisoformat(now()):
        return jsonify(error='撤销时间已过，可从完整备份找回。'),410
    word=snapshot['vocabulary'][0]
    if db.execute('SELECT 1 FROM vocabulary WHERE id=? OR word=? COLLATE NOCASE',(word['id'],word['word'])).fetchone():
        return jsonify(error='相同词条已重新创建，请先整理新词条后再撤销，避免覆盖。'),409
    if word['article_id'] and not db.execute('SELECT 1 FROM articles WHERE id=?',(word['article_id'],)).fetchone():word['article_id']=None
    for table in ('vocabulary',*CHILDREN):
        for item in snapshot[table]:
            if table=='word_memberships' and not db.execute('SELECT 1 FROM word_collections WHERE id=?',(item['collection_id'],)).fetchone():continue
            if table=='word_contexts' and item['article_id'] and not db.execute('SELECT 1 FROM articles WHERE id=?',(item['article_id'],)).fetchone():item['article_id']=None
            values=[base64.b64decode(v['bytes']) if isinstance(v,dict) and set(v)=={'bytes'} else v for v in item.values()]
            db.execute('INSERT INTO '+table+'('+','.join('"'+k+'"' for k in item)+') VALUES('+','.join('?' for _ in values)+')',values)
    for event in snapshot['events']:db.execute('UPDATE srs_events SET word_id=? WHERE id=? AND word_id IS NULL',(word['id'],event))
    db.execute('DELETE FROM extension_records WHERE id=?',(token,));db.commit()
    return jsonify(ok=True,word_id=word['id'])
