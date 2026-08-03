# ตั้งค่า Google OAuth สำหรับ Cherry Mail Memory

## 1. สร้าง Google Cloud project

เปิด Google Cloud Console แล้วสร้าง project ใหม่ หรือใช้ project ภายในองค์กรที่มีอยู่

## 2. เปิด Gmail API

ไปที่ API Library ค้นหา **Gmail API** แล้วกด Enable

## 3. ตั้ง Google Auth Platform

กำหนดข้อมูลใน Branding, Audience และ Data Access

- ใช้ **Internal** สำหรับบัญชีใน Google Workspace องค์กรเดียวกัน
- ใช้ **External** หากต้องการรองรับ Gmail ทั่วไป
- ถ้า External app ยังอยู่ใน Testing ให้เพิ่ม Gmail ที่จะทดสอบไว้ใน Test users

## 4. สร้าง OAuth Client

ไปที่ Clients แล้วสร้าง client ใหม่:

```text
Application type: Desktop app
Name: Cherry Mail Memory Windows
```

สำหรับการแจกโปรแกรมข้ามแพลตฟอร์ม ควรสร้าง Desktop client แยก Windows และ Linux แล้วใช้ไฟล์ credentials ให้ตรงกับ build ที่แจก

## 5. ดาวน์โหลด JSON

ดาวน์โหลดไฟล์ client JSON เก็บไว้ในเครื่อง เช่น:

```text
C:\Secure\cherry-gmail-windows.json
/home/user/secure/cherry-gmail-linux.json
```

ไม่ต้องนำไฟล์นี้ใส่ใน source code หรือ installer สาธารณะ

## 6. เชื่อมบัญชีในแอป

1. เปิด Cherry Mail Memory
2. กด **เชื่อม Gmail**
3. เลือก JSON
4. เบราว์เซอร์จะเปิดหน้า Google OAuth
5. เลือกบัญชีและอนุญาตสิทธิ์อ่าน Gmail
6. แอปจะเก็บ refresh token ในโฟลเดอร์ข้อมูลของผู้ใช้
7. Full sync เริ่มอัตโนมัติ

## Scope ที่ใช้

```text
https://www.googleapis.com/auth/gmail.readonly
```

Scope นี้อ่าน messages, labels, profile และ mailbox history ได้ แต่ไม่สามารถแก้ไขหรือลบอีเมล

## ปัญหาที่พบบ่อย

### Error 403: access_denied

- External app ใน Testing แต่ Gmail ไม่ได้อยู่ใน Test users
- ผู้ดูแล Google Workspace บล็อก third-party OAuth app
- OAuth consent screen ตั้งไม่ครบ

### Error 400: redirect_uri_mismatch

ตรวจว่า client type เป็น **Desktop app** ไม่ใช่ Web application

### Token ใช้ไม่ได้หลังเปลี่ยน scope

ลบบัญชี/ไฟล์ token เดิมแล้วเชื่อมใหม่ เนื่องจาก token เก่าผูกกับ scope เดิม

### historyId expired / HTTP 404

ไม่ใช่ token เสีย Gmail เก็บ history ไว้จำกัดเวลา แอปจะทำ full sync ใหม่และบันทึก historyId ล่าสุดโดยอัตโนมัติ
