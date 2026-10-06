# VAPT Login Creds

## App / API (`POST /auth/login`)

Send `"realm":"vapt"`.

| Username | Password |
|---|---|
| `manish-vapl` | `manishkumar@123` |
| `himanshudevops` | `himanshu@123` |

```bash
curl -X POST http://localhost/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"himanshudevops","password":"himanshu@123","realm":"vapt"}'
```

## Redmine — VAPT TOOL project (`http://localhost:3000`)

| Login | Password |
|---|---|
| `manish-vapl` | `manishkumar@123` |
| `himanshudevops` | `himanshu@123` |
