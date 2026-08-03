# Cherry Mail Memory

Desktop app สำหรับ **Windows และ Linux** ที่เก็บ Gmail ลงฐานข้อมูลในเครื่อง ค้นหาอีเมลย้อนหลัง และใช้ข้อมูลอีเมลเป็น RAG memory ให้ AI ผ่าน API แบบ OpenAI-compatible เช่น Ollama, vLLM หรือเซิร์ฟเวอร์โมเดลภายในองค์กร

## ความสามารถหลัก

- เชื่อม Gmail ด้วย OAuth 2.0 แบบ Desktop app และใช้สิทธิ์ `gmail.readonly`
- Full sync ทุกข้อความ รวม Spam และ Trash
- เก็บหัวข้อ ผู้ส่ง ผู้รับ เนื้อหา HTML/text, labels, metadata และ MIME ดิบ
- เก็บไฟล์แนบไว้ใน MIME ดิบ และเลือกแยกไฟล์แนบออกมาบนดิสก์ได้
- Incremental sync เฉพาะเมล/label ที่เปลี่ยนผ่าน Gmail `historyId`
- ถ้า `historyId` เก่าเกินไป แอปจะกลับไปทำ full sync อัตโนมัติ
- SQLite แบบ WAL พร้อม FTS5 โดยพยายามใช้ trigram tokenizer เพื่อค้นภาษาไทยและข้อความบางส่วน
- ค้นด้วยข้อความธรรมดา หรือ filter แบบ `from:`, `to:`, `subject:`, `label:`, `after:`, `before:`
- Hybrid RAG: lexical search + vector similarity
- เก็บ embeddings เป็น `float32` ใน SQLite และโหลดเป็น NumPy vector index เมื่อถาม AI
- Chat history แยกตามบัญชี Gmail
- คำตอบ AI อ้างอิงหลักฐานกลับไปยังอีเมลต้นทาง
- Export อีเมลกลับเป็น `.eml`
- GitHub Actions สร้าง artifact สำหรับ Windows และ Linux

## หน้าตาระบบ

แอปแบ่งเป็น 3 ส่วน:

1. **ค้นหาอีเมล** แสดงรายการ อ่านเนื้อหา ดูไฟล์แนบ และ export `.eml`
2. **AI Memory** ถามคำถามจากอีเมลและดูรายการหลักฐานที่ระบบนำไปตอบ
3. **ระบบและตั้งค่า** ดูสถิติ archive, ตั้งช่วงเวลา sync และกำหนด local AI endpoint

## ติดตั้งเพื่อพัฒนา

ต้องใช้ Python 3.11 หรือใหม่กว่า

```bash
git clone https://github.com/paddman/Cherry-Gmail-to-database.git
cd Cherry-Gmail-to-database
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .[dev]
python -m cherry_mail_memory
```

Linux:

```bash
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e '.[dev]'
python -m cherry_mail_memory
```

## ตั้งค่า Google OAuth

ดูขั้นตอนละเอียดที่ [docs/GOOGLE_OAUTH_SETUP.md](docs/GOOGLE_OAUTH_SETUP.md)

สรุปขั้นตอน:

1. สร้าง Google Cloud project
2. Enable Gmail API
3. ตั้ง OAuth consent screen
4. สร้าง OAuth Client ID ชนิด **Desktop app**
5. ดาวน์โหลดไฟล์ JSON
6. เปิด Cherry Mail Memory แล้วกด **เชื่อม Gmail**
7. เลือก JSON และอนุญาตสิทธิ์ผ่านเบราว์เซอร์

ห้าม commit `credentials.json` หรือ token ลง Git repository โดยเด็ดขาด ไฟล์เหล่านี้ถูกเพิ่มไว้ใน `.gitignore` แล้ว

## ตั้งค่า Local AI

เปิดแท็บ **ระบบและตั้งค่า** แล้วกำหนด:

- Chat base URL เช่น `http://127.0.0.1:11434/v1`
- Chat model เป็น model ID ที่เซิร์ฟเวอร์ของคุณให้บริการ
- Embedding base URL
- Embedding model
- API key ถ้า endpoint บังคับใช้

จากนั้นกด **สร้าง AI Index** หลัง full sync ครั้งแรก เมลใหม่จะถูกสร้าง embedding อัตโนมัติหลัง sync เมื่อเปิดตัวเลือก Auto Index

แอปไม่ bundle โมเดล AI เพื่อให้อินสตอลเลอร์มีขนาดเล็ก และเปิดให้เลือกใช้ Local AI หรือบริการ OpenAI-compatible ตามสภาพแวดล้อมของผู้ใช้

## รูปแบบการค้นหา

```text
ระบบสำรองข้อมูล
from:alice@example.com
subject:"ใบเสนอราคา"
to:finance@example.com after:2026-01-01
label:IMPORTANT before:2026-08-01
from:vendor@example.com สัญญา renewal
```

ตัวกรองใช้ร่วมกับข้อความค้นได้

## วิธี Sync

### ครั้งแรก

1. `users.messages.list` ไล่ทุกหน้าโดยเปิด `includeSpamTrash`
2. ดึงข้อความแบบ `format=raw` เป็นชุด
3. ถอด MIME, สร้างข้อความสำหรับค้น และเก็บ raw MIME ลง SQLite
4. สร้าง RAG chunks
5. บันทึก mailbox `historyId`

### ครั้งต่อไป

1. เรียก `users.history.list` จาก `historyId` ล่าสุด
2. ดึงเฉพาะข้อความที่เพิ่มหรือเปลี่ยน label
3. ทำเครื่องหมายข้อความที่ถูกลบจาก Gmail แต่ยังเก็บ raw archive ไว้ในฐานข้อมูล
4. อัปเดต `historyId`
5. สร้าง embeddings เฉพาะ chunks ใหม่

Desktop app ใช้ polling ตามช่วงเวลาที่ตั้งไว้ และจะอัปเดตอัตโนมัติขณะเปิดแอป

## ตำแหน่งข้อมูล

Windows โดยทั่วไป:

```text
%LOCALAPPDATA%\paddman\CherryMailMemory
```

Linux โดยทั่วไป:

```text
~/.local/share/CherryMailMemory
```

ข้อมูลสำคัญ:

```text
mail_memory.db          SQLite archive + FTS + vectors + chat history
tokens/                 OAuth refresh tokens
attachments/            ไฟล์แนบที่เลือกแยกออกจาก MIME
logs/                    application logs
```

## Build โปรแกรม

Windows:

```powershell
.\scripts\build_windows.ps1
```

Linux:

```bash
chmod +x scripts/build_linux.sh
./scripts/build_linux.sh
```

ผลลัพธ์อยู่ใน `dist/CherryMailMemory/`

GitHub Actions workflow ที่ `.github/workflows/build.yml` จะทดสอบและสร้าง artifact แยก Windows/Linux เมื่อ push เข้า `main`, สร้าง tag หรือกด Workflow Dispatch

## ทดสอบ

```bash
pytest
```

ชุดทดสอบครอบคลุม MIME parsing, attachment metadata, chunking, SQLite archive, FTS search, raw `.eml`, deleted archive และ RAG retrieval

## ความปลอดภัยและข้อจำกัด

- OAuth scope เป็น read-only แอปไม่ส่ง ลบ หรือแก้ไขอีเมลใน Gmail
- Token, API key และฐานข้อมูลอยู่ในเครื่อง ผู้ใช้ต้องดูแลสิทธิ์ไฟล์และ disk encryption
- HTML mail แสดงเป็น plain text เพื่อลด remote tracking และ script risk
- เนื้อหาอีเมลถูกถือเป็นข้อมูลที่ไม่น่าเชื่อถือใน system prompt เพื่อลด prompt injection
- การเปิดให้ผู้ใช้ภายนอกองค์กรจำนวนมากอาจต้องผ่านกระบวนการ OAuth verification ของ Google
- Initial sync ของ mailbox ใหญ่ใช้เวลาและพื้นที่ดิสก์ตามจำนวน/ขนาดอีเมล
- Vector index ปัจจุบันโหลด embeddings ของบัญชีที่เลือกเข้า RAM เหมาะกับ local mailbox ระดับทั่วไป หากโตถึงหลายล้าน chunks ควรย้าย vector layer ไป Qdrant หรือ PostgreSQL/pgvector

ดูสถาปัตยกรรมเพิ่มเติมที่ [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
