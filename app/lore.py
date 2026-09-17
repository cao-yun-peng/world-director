"""A07 不可变设定快照；字符位置基于 LF 规范化后的原段，左闭右开。"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

SUBJECTS = frozenset({'player', 'lin_yan', 'other_npc', 'archive_keeper'})
LORE_VERSION = 'a07-lore-v1'
CHUNK_VERSION = 'paragraph-codepoint-400-v1'
SEED_PATH = Path(__file__).resolve().parent.parent / 'data' / 'lore' / 'handover.json'


def text_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def content_hash(title, paragraphs):
    return text_hash(json.dumps([title, paragraphs], ensure_ascii=False, separators=(',', ':')))


@dataclass(frozen=True)
class LoreSource:
    source_id: str
    source_version: str
    scenario_id: str
    title: str
    audience: frozenset[str]
    paragraphs: tuple[str, ...]
    origin: str
    content_hash: str


@dataclass(frozen=True)
class LoreChunk:
    source_id: str
    source_version: str
    scenario_id: str
    title: str
    audience: frozenset[str]
    paragraph: int
    start: int
    end: int
    text: str
    text_hash: str
    chunk_id: str

    def reference(self):
        return {key: getattr(self, key) for key in (
            'source_id', 'source_version', 'chunk_id', 'paragraph', 'start', 'end', 'text_hash')}


@dataclass(frozen=True)
class LoreSnapshot:
    version: str
    sources: tuple[LoreSource, ...]
    chunks: tuple[LoreChunk, ...]


def load_records(records, *, version=LORE_VERSION, max_chunks=24):
    if not isinstance(records, list) or not isinstance(version, str) or not version.strip():
        raise ValueError('INVALID_LORE')
    fields = {'source_id', 'source_version', 'scenario_id', 'title', 'audience',
              'paragraphs', 'origin', 'content_hash'}
    sources = {}
    for record in records:
        if not isinstance(record, dict) or set(record) != fields:
            raise ValueError('INVALID_LORE_FIELDS')
        if any(not isinstance(record[k], str) or not record[k].strip()
               for k in fields - {'paragraphs', 'audience'}):
            raise ValueError('INVALID_LORE_METADATA')
        if record['source_version'] != version:
            raise ValueError('LORE_VERSION_MISMATCH')
        audience = record['audience']
        if (not isinstance(audience, list) or not audience
                or not all(isinstance(s, str) and s in SUBJECTS for s in audience)
                or len(set(audience)) != len(audience)):
            raise ValueError('INVALID_LORE_AUDIENCE')
        paragraphs = record['paragraphs']
        if not isinstance(paragraphs, list) or not all(isinstance(p, str) for p in paragraphs):
            raise ValueError('INVALID_LORE_PARAGRAPHS')
        paragraphs = tuple(p.replace('\r\n', '\n').replace('\r', '\n') for p in paragraphs)
        if not any(p.strip() for p in paragraphs):
            raise ValueError('EMPTY_LORE')
        if record['content_hash'] != content_hash(record['title'], paragraphs):
            raise ValueError('LORE_HASH_MISMATCH')
        source = LoreSource(**{**record, 'audience': frozenset(audience), 'paragraphs': paragraphs})
        key = (source.source_id, source.source_version)
        if key in sources and sources[key] != source:
            raise ValueError('CONFLICTING_LORE_SOURCE')
        sources[key] = source
    chunks = []
    for source in sorted(sources.values(), key=lambda s: s.source_id):
        for paragraph, text in enumerate(source.paragraphs):
            if not text.strip():
                continue
            for start in range(0, len(text), 400):
                body = text[start:start + 400]
                if not body.strip():
                    continue
                digest = text_hash(body)
                identity = [CHUNK_VERSION, source.source_id, source.source_version, source.scenario_id,
                            source.title, sorted(source.audience), paragraph, start, digest]
                chunk_id = 'lc-' + text_hash(json.dumps(identity, ensure_ascii=False))[:24]
                chunks.append(LoreChunk(source.source_id, source.source_version, source.scenario_id,
                    source.title, source.audience, paragraph, start, start + len(body), body, digest, chunk_id))
    if len(chunks) > max_chunks:
        raise ValueError('LORE_CHUNK_LIMIT')
    return LoreSnapshot(version, tuple(sorted(sources.values(), key=lambda s: s.source_id)), tuple(chunks))


def load_lore(path=SEED_PATH):
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(payload, dict) or set(payload) != {'version', 'sources'}:
        raise ValueError('INVALID_LORE_SNAPSHOT')
    return load_records(payload['sources'], version=payload['version'])


def authorized_chunks(snapshot, *, actor_id, recipient_id, scenario_id):
    """唯一 ACL 入口。受控私有单测可令 recipient=actor；生成路径固定 player。"""
    if actor_id not in SUBJECTS - {'player'} or recipient_id not in SUBJECTS:
        raise ValueError('UNKNOWN_LORE_SUBJECT')
    return tuple(chunk for chunk in snapshot.chunks
                 if chunk.source_version == snapshot.version and chunk.scenario_id == scenario_id
                 and actor_id in chunk.audience and recipient_id in chunk.audience)


def validate_query(arguments):
    if not isinstance(arguments, dict) or set(arguments) != {'query', 'top_k'}:
        raise ValueError('INVALID_ARGUMENTS')
    query, top_k = arguments['query'], arguments['top_k']
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
        raise ValueError('INVALID_ARGUMENTS')
    if type(top_k) is not int or not 1 <= top_k <= 3:
        raise ValueError('INVALID_ARGUMENTS')
    return query.strip(), top_k
