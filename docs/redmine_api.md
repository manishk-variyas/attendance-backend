## 📊 Project APIs

### 23. Create/Update Project

```bash
curl -X POST http://localhost:8000/api/projects \
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
curl -X GET "http://localhost:8000/api/projects/test@gmail.com" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

---

## 👥 Redmine Integration APIs (User Projects)

### 25. Get User Projects (by Email)

```bash
curl -X GET "http://localhost:8000/api/user-projects/email/test@gmail.com"
```

### 26. Get User Projects (by ID)

```bash
curl -X GET "http://localhost:8000/api/user-projects/id/123"
```

### 27. Get All Users with Projects

```bash
curl -X GET "http://localhost:8000/api/user-projects"
```

---

## 📋 Issue Integration APIs

### 29. Get User Issues (by Email)

```bash
curl -X GET "http://localhost:8000/api/user-issues/email/test@gmail.com"
```

### 30. Get User Issues (by ID)

```bash
curl -X GET "http://localhost:8000/api/user-issues/id/123"
```

---

## 🔍 Health Check

### 28. Server Health

```bash
curl -X GET "http://localhost:8000/"
```

---

## 💻 Client-Side Integration

To use these endpoints in a frontend application (e.g., React, Vue, or Vanilla JS), ensure you include the session cookie using `credentials: "include"`.

### Example: Fetch User Projects

```javascript
const API_BASE = "http://localhost:8000";

async function getUserProjects(email) {
  try {
    const response = await fetch(`${API_BASE}/api/projects/${email}`, {
      method: "GET",
      credentials: "include", // Required to send the session cookie
      headers: {
        "Content-Type": "application/json"
      }
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Failed to fetch projects");
    }

    return await response.json();
  } catch (err) {
    console.error("Error fetching projects:", err);
  }
}
```

### Example: Create Project

```javascript
async function createProject(projectData) {
  const response = await fetch(`${API_BASE}/api/projects`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(projectData)
  });
  
  return await response.json();
}
```
