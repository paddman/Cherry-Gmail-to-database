# Architecture

## Component map

```text
PySide6 Desktop UI
├── Account / OAuth
├── Mail Search + Reader + EML Export
├── AI Memory Chat + Sources
└── Settings / Sync Status
        │
        ├── GmailClient
        │   ├── OAuth InstalledAppFlow
        │   ├── messages.list + messages.get(format=raw)
        │   └── history.list incremental sync
        │
        ├── SyncService
        │   ├── MIME parser
        │   ├── raw archive
        │   ├── attachment extraction
        │   └── chunk generation
        │
        ├── Database (SQLite WAL)
        │   ├── accounts
        │   ├── messages + raw MIME
        │   ├── attachments
        │   ├── FTS5 message/chunk indexes
        │   ├── float32 embeddings
        │   └── chat sessions/history
        │
        └── RAGEngine
            ├── FTS retrieval
            ├── NumPy cosine retrieval
            ├── hybrid ranking
            └── OpenAI-compatible chat/embedding APIs
```

## Database behavior

- `messages` ใช้ `(account_id, message_id)` เป็น primary key
- Gmail message body ถือว่า immutable โดยทั่วไป จึงใช้ `content_hash` เพื่อลดการสร้าง chunks/embeddings ใหม่เมื่อเปลี่ยนแค่ label
- MIME ดิบเก็บเป็น BLOB และบีบอัดระดับต่ำเฉพาะเมื่อช่วยลดขนาดได้จริง
- เมลที่หายจาก Gmail ถูกตั้ง `is_deleted=1` แต่ยังเก็บ raw MIME เพื่อคงคุณสมบัติ archive
- FTS และ vector retrieval กรองเฉพาะ `is_deleted=0`
- SQLite เปิด WAL, foreign keys, busy timeout และ transaction ต่อ batch

## RAG flow

```text
Question
  ├── FTS5 top candidates
  ├── Query embedding
  ├── NumPy cosine top candidates
  └── Weighted merge
          │
          ▼
   Source chunks + metadata
          │
          ▼
 OpenAI-compatible chat model
          │
          ▼
 Answer with [1], [2] citations
```

น้ำหนักเริ่มต้น:

```text
Vector similarity 65%
Lexical ranking 35%
```

Email excerpts ถูกครอบด้วย system policy ว่าเป็น untrusted data เพื่อลดการที่ข้อความในอีเมลพยายามสั่งโมเดล

## Sync state

```text
No historyId ───────────────► Full Sync
                                 │
                                 ▼
                           Save historyId
                                 │
                                 ▼
Timer / Sync button ───────► Incremental Sync
                                 │
                   historyId valid? ── yes ─► update changed messages
                                 │
                                 no / 404
                                 ▼
                              Full Sync
```

## Scaling path

MVP เก็บทุกอย่างใน local SQLite เพื่อให้ติดตั้งง่ายและไม่ต้องมี server เพิ่ม หากข้อมูลโตมาก:

1. ย้าย raw archive ไป object storage เช่น MinIO
2. ย้าย metadata/search ไป PostgreSQL
3. ย้าย embeddings ไป pgvector หรือ Qdrant
4. ใช้ Gmail push notification ผ่าน Pub/Sub แทน desktop polling
5. แยก ingestion worker จาก desktop UI

โครงสร้าง service ปัจจุบันแยก Gmail, storage และ RAG ไว้แล้ว จึงย้ายทีละส่วนได้โดยไม่ต้องเขียน UI ใหม่ทั้งหมด
