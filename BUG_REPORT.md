# UniMart Bug Report & Fixes

## Summary
Found **3 bugs** in your productivity app, with 2 critical issues and 1 styling issue.

---

## BUG #1 (CRITICAL) - Line 943 in [script.js](script.js#L943)
### Issue: Missing email fallback in order tracking - causes API failure
**Severity:** CRITICAL - Tracking feature breaks if user has no phone number

### Current Code:
```javascript
const res = await authFetch(`${API_URL}/orders/history?phone=${currentUser.phone}`);
```

### Problem:
- If `currentUser.phone` is `null` or `undefined`, the query string becomes `?phone=undefined`
- API endpoint receives "undefined" as a string and rejects it in the validation check
- Order tracking modal won't work for users without phone numbers

### Fix:
```javascript
const res = await authFetch(`${API_URL}/orders/history?phone=${currentUser.phone || currentUser.email}`);
```

---

## BUG #2 (CRITICAL) - Line 1038 in [script.js](script.js#L1038)
### Issue: Same as Bug #1 - Missing email fallback in AI recommendations
**Severity:** CRITICAL - AI recommendations feature breaks if user has no phone

### Current Code:
```javascript
const hRes = await authFetch(`${API_URL}/orders/history?phone=${currentUser.phone}`);
```

### Problem:
- Identical to Bug #1
- AI recommendation feature fails to load order history for users without phone
- Prevents personalized product recommendations

### Fix:
```javascript
const hRes = await authFetch(`${API_URL}/orders/history?phone=${currentUser.phone || currentUser.email}`);
```

---

## BUG #3 (MODERATE) - Line 217 in [script.js](script.js#L217)
### Issue: Toast notification doesn't support "info" type
**Severity:** MODERATE - Visual/UX issue, not functional

### Current Code:
```javascript
// Line 217:
showToast("Using Mock Google Login bypass...", "info");

// Line 777 - showToast function:
function showToast(msg, type = 'success') {
    // ...
    if (type === 'success') {
        // GREEN styling for success
        toast.classList.add('border-green-500');
        // ...
    } else {
        // RED ERROR styling for everything else
        toast.classList.add('border-red-500');
        // ...
    }
}
```

### Problem:
- `showToast()` is called with type `"info"` at line 217
- But the function only has two states: `success` (green) and `else` (red error)
- The info message gets styled as an ERROR (red exclamation icon)
- Misleads users into thinking something went wrong during mock login

### Fix Options:

**Option A** - Add info type support:
```javascript
function showToast(msg, type = 'success') {
    const toast = document.getElementById('toast');
    const title = document.getElementById('toast-title');
    const message = document.getElementById('toast-message');
    const iconContainer = document.getElementById('toast-icon-container');
    const icon = document.getElementById('toast-icon');

    toast.className = "fixed top-24 right-4 md:right-10 z-[90] bg-white border-l-4 shadow-2xl rounded-lg p-4 flex items-center gap-4 transition-all duration-500 transform translate-x-full opacity-0 pointer-events-none min-w-[300px]";
    iconContainer.className = "w-8 h-8 rounded-full flex items-center justify-center shrink-0";

    if (type === 'success') {
        toast.classList.add('border-green-500');
        iconContainer.classList.add('bg-green-100', 'text-green-600');
        icon.className = "fa-solid fa-check";
        title.innerText = "Success";
        title.className = "font-bold text-gray-800 text-sm";
    } else if (type === 'info') {
        toast.classList.add('border-blue-500');
        iconContainer.classList.add('bg-blue-100', 'text-blue-600');
        icon.className = "fa-solid fa-info-circle";
        title.innerText = "Info";
        title.className = "font-bold text-blue-600 text-sm";
    } else {
        toast.classList.add('border-red-500');
        iconContainer.classList.add('bg-red-100', 'text-red-600');
        icon.className = "fa-solid fa-circle-exclamation";
        title.innerText = "Error";
        title.className = "font-bold text-red-600 text-sm";
    }
    message.innerText = msg;
    // ... rest of function
}
```

**Option B** - Change the call to use 'success' if it's just informational:
```javascript
showToast("Using Mock Google Login bypass...", "success");
```

---

## Impact Analysis

| Bug | Feature Affected | User Impact | Data Loss |
|-----|------------------|------------|-----------|
| #1  | Order Tracking | Tracking modal won't load for users without phone | No |
| #2  | AI Recommendations | Recommendations won't load for users without phone | No |
| #3  | UI/UX | Info messages appear as errors | No |

---

## Recommended Priority
1. **Fix Bug #1 & #2 FIRST** (same fix in both places) - Critical for functionality
2. **Fix Bug #3** - Polish/UX improvement

All fixes are low-risk and don't affect database or authentication.
