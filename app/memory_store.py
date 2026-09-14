"""Opt-in single-user localhost persistence. JSON + SQLite, never pickle."""
from __future__ import annotations
import json
from pathlib import Path
import sqlite3
from datetime import datetime, timezone

DEFAULT_PATH = Path(__file__).resolve().parents[1] / '.local' / 'memory.sqlite3'


class MemoryStore:
    def __init__(self, path=DEFAULT_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, updated TEXT NOT NULL, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, body TEXT NOT NULL)')

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def save(self, session_id, memory, messages):
        body = json.dumps({'version':2,'memory':memory.to_dict(),'messages':messages[-40:]}, ensure_ascii=False)
        if len(body.encode('utf-8')) > 2000000:
            raise ValueError('LocalMemoryTooLarge')
        with self.connect() as db:
            db.execute('INSERT INTO sessions VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated, body=excluded.body',
                       (session_id, datetime.now(timezone.utc).isoformat(), body))
            for pet in memory.pets.values():
                # Only stable profile data crosses into a NEW conversation.
                profile = {'id':pet.id,'name':pet.name,'species':pet.species,'profile':pet.profile}
                db.execute('INSERT INTO profiles VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                           (pet.id,json.dumps(profile,ensure_ascii=False)))

    def list_sessions(self):
        with self.connect() as db:
            return db.execute('SELECT id,updated FROM sessions ORDER BY updated DESC LIMIT 50').fetchall()

    def load(self, session_id):
        with self.connect() as db:
            row = db.execute('SELECT body FROM sessions WHERE id=?',(session_id,)).fetchone()
        if row is None:
            raise KeyError('SessionNotFound')
        data = json.loads(row[0])
        if data.get('version') != 2:
            raise ValueError('UnsupportedMemoryVersion')
        return data

    def profiles(self):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT body FROM profiles')]

    def clear(self):
        """Called only from the explicit, confirmed local-memory removal UI."""
        with self.connect() as db:
            db.execute('DELETE FROM sessions')
            db.execute('DELETE FROM profiles')
