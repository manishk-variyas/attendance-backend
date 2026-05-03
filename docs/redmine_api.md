
## 📊 Project APIs

### 23. Create/Update Project

```bash
curl -X POST http://localhost:5000/api/projects \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "email": "test@gmail.com",
    "customerName": "AXIS",
    "city": "Delhi",
    "customerOfficeLocation": "Gurgaon Office",
    "projectType": "Onsite",
    "status": "present_onsite"
  }'
```

### 24. Get User Project

```bash
curl -X GET "http://localhost:5000/api/projects/test@gmail.com" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

---

## 👥 Redmine Integration APIs (User Projects)

### 25. Get User Projects (by Email)

```bash
curl -X GET "http://localhost:5000/api/user-projects/email/test@gmail.com"
```

### 26. Get User Projects (by ID)

```bash
curl -X GET "http://localhost:5000/api/user-projects/id/123"
```

### 27. Get All Users with Projects

```bash
curl -X GET "http://localhost:5000/api/user-projects"
```

---

## 📋 Issue Integration APIs

### 29. Get User Issues (by Email)

```bash
curl -X GET "http://localhost:5000/api/user-issues/email/test@gmail.com"
```

### 30. Get User Issues (by ID)

```bash
curl -X GET "http://localhost:5000/api/user-issues/id/123"
```

---

## 🔍 Health Check

### 28. Server Health

```bash
curl -X GET "http://localhost:5000/"
```

---

