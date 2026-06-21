// ============================================================
// 1. GLOBAL VARIABLES & KEYS  (API_URL, DELIVERY_FEE, keys → config.js)
// ============================================================
let currentUser = null;
let cart = [];
let activeShopId = 'all';
let products = [];
let map, marker, geocoder;
let trackingInterval;
let currentAuthRole = 'student';
let currentSignupRole = 'student';
let supabaseClient = null;

// SECURITY: HTML sanitizer to prevent XSS injection
function sanitize(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// SECURITY: Wrapper for fetch with JWT and automated retry logic to avoid network dropouts
async function authFetch(url, options = {}, retries = 3, delay = 1000) {
    const token = localStorage.getItem('uniMartToken');
    if (token) {
        options.headers = {
            ...options.headers,
            'Authorization': `Bearer ${token}`
        };
    }
    for (let i = 0; i < retries; i++) {
        try {
            return await fetch(url, options);
        } catch (err) {
            if (i === retries - 1) throw err;
            await new Promise(res => setTimeout(res, delay * Math.pow(2, i)));
        }
    }
}

function saveCart() {
    localStorage.setItem('uniMartCart', JSON.stringify(cart));
}

function loadCart() {
    try {
        const saved = localStorage.getItem('uniMartCart');
        if (saved) cart = JSON.parse(saved);
    } catch (e) {
        cart = [];
    }
}

function sendOrderConfirmationEmail(emailParams, onComplete) {
    const orderTemplate = (typeof ORDER_TEMPLATE_ID !== 'undefined') ? ORDER_TEMPLATE_ID : TEMPLATE_ID;
    if (typeof emailjs !== 'undefined' && PUBLIC_KEY && SERVICE_ID && orderTemplate) {
        emailjs.send(SERVICE_ID, orderTemplate, emailParams).then(onComplete, onComplete);
    } else {
        onComplete();
    }
}

// ============================================================
// 2. STARTUP LOGIC
// ============================================================
document.addEventListener('DOMContentLoaded', async () => {
    // Toggle Mock Login Developer Bypass based on host
    const mockLoginContainer = document.getElementById('mock-login-container');
    if (mockLoginContainer) {
        if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
            mockLoginContainer.classList.remove('hidden');
        } else {
            mockLoginContainer.classList.add('hidden');
        }
    }

    // Initialize Supabase Client
    if (typeof supabase !== 'undefined' && typeof SUPABASE_URL !== 'undefined' && SUPABASE_ANON_KEY !== 'YOUR_SUPABASE_ANON_KEY') {
        try {
            supabaseClient = supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
        } catch (e) {
            console.error("Supabase Init Error:", e);
        }
    }

    if (typeof emailjs !== 'undefined' && PUBLIC_KEY) {
        try { emailjs.init(PUBLIC_KEY); } catch (e) { console.error("EmailJS Error:", e); }
    }

    loadCart();

    // Detect redirect callback and get Supabase session
    if (supabaseClient) {
        const { data: { session }, error } = await supabaseClient.auth.getSession();
        if (session) {
            const activeUser = localStorage.getItem('uniMartActiveUser');
            if (!activeUser) {
                const user = session.user;
                const email = user.email;
                const full_name = user.user_metadata?.full_name || user.user_metadata?.name || 'New User';
                const avatar_url = user.user_metadata?.avatar_url || user.user_metadata?.picture || '';
                
                let selectedRole = localStorage.getItem('selectedRoleForGoogleAuth') || 'student';
                if (selectedRole === 'delivery') selectedRole = 'partner';
                
                try {
                    const res = await fetch(`${API_URL}/auth/google-login`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            email: email,
                            name: full_name,
                            avatar_url: avatar_url,
                            selected_role: selectedRole
                        })
                    });
                    const data = await res.json();
                    if (res.ok && data.status === 'success') {
                        localStorage.setItem('uniMartToken', data.token);
                        localStorage.setItem('uniMartActiveUser', JSON.stringify(data.user));
                        localStorage.setItem('uniActiveRole', data.user.role);
                        localStorage.removeItem('selectedRoleForGoogleAuth');
                        
                        currentUser = data.user;
                        showToast("Signed in as " + currentUser.name);
                        
                        setTimeout(() => {
                            if (currentUser.role === 'admin') {
                                window.location.href = 'admin_dashboard.html';
                            } else if (currentUser.role === 'vendor') {
                                window.location.href = 'vendor_dashboard.html';
                            } else if (currentUser.role === 'partner' || currentUser.role === 'delivery') {
                                window.location.href = 'delivery_dashboard.html';
                            } else {
                                window.location.href = 'index.html';
                            }
                        }, 1000);
                        return;
                    } else {
                        showToast(data.detail || "Google login registration failed", "error");
                    }
                } catch (e) {
                    console.error("Backend OAuth syncing failed:", e);
                    showToast("Sync Error: " + e.message, "error");
                }
            }
        }
    }

    // 2. Check for Active Session
    const sessionUser = JSON.parse(localStorage.getItem('uniMartActiveUser'));
    if (sessionUser) {
        currentUser = sessionUser;
        initSession(currentUser);
    } else {
        updateNavUI();
    }

    // 3. Render Shops & Fetch Products
    renderShops();
    fetchProductsFromDB();
    updateCartUI();
});

// --- NEW: LOAD PRODUCTS FROM DATABASE ---
async function fetchProductsFromDB() {
    const grid = document.getElementById('product-grid');
    grid.innerHTML = '<div class="col-span-full text-center py-10"><i class="fa-solid fa-spinner fa-spin text-4xl text-secondary"></i><p class="mt-2 text-gray-500">Loading products...</p></div>';

    try {
        const response = await fetch(`${API_URL}/products?t=${new Date().getTime()}`);
        const data = await response.json();

        if (Array.isArray(data) && data.length > 0) {
            products = data.filter(p => p.approval_status === 'Approved').map(p => ({
                ...p,
                shop_id: p.shop_id || p.shopId,
                in_stock: p.in_stock !== undefined ? parseInt(p.in_stock) : 1
            }));
            filterProducts('all');
            if (currentUser) fetchAIRecommendations();
        } else {
            grid.innerHTML = '<div class="col-span-full text-center py-10"><p>No products found in database.</p></div>';
        }
    } catch (error) {
        console.error("Error loading products:", error);
        grid.innerHTML = '<div class="col-span-full text-center py-10 text-red-500"><p>Failed to load products from DB.</p></div>';
    }
}

function openAuthModal(role = 'student') { 
    setAuthRole(role);
    
    // Reset login form fields and their visibility
    const nameCont = document.getElementById('name-input-container');
    const phoneCont = document.getElementById('phone-input-container');
    const emailCont = document.getElementById('email-input-container');
    const otpCont = document.getElementById('otp-input-container');
    
    if (nameCont) nameCont.classList.remove('hidden');
    if (phoneCont) phoneCont.classList.remove('hidden');
    if (emailCont) emailCont.classList.remove('hidden');
    if (otpCont) otpCont.classList.add('hidden');
    
    const nameInput = document.getElementById('auth-name');
    const phoneInput = document.getElementById('auth-phone');
    const emailInput = document.getElementById('auth-email');
    const otpInput = document.getElementById('auth-otp');
    const btnText = document.getElementById('auth-btn-text');
    const btnIcon = document.getElementById('auth-btn-icon');
    
    if (nameInput) nameInput.value = '';
    if (phoneInput) phoneInput.value = '';
    if (emailInput) emailInput.value = '';
    if (otpInput) otpInput.value = '';
    if (btnText) btnText.innerText = "Get Verification Code";
    if (btnIcon) btnIcon.className = "fa-solid fa-paper-plane text-lg text-[#FFB703]";
    
    const modal = document.getElementById('auth-modal');
    const card = document.getElementById('auth-card');
    modal.classList.remove('hidden');
    setTimeout(() => {
        card.classList.remove('scale-95', 'opacity-0');
        card.classList.add('scale-100', 'opacity-100');
    }, 10);
}

function closeAuthModal() { 
    const modal = document.getElementById('auth-modal');
    const card = document.getElementById('auth-card');
    card.classList.add('scale-95', 'opacity-0');
    card.classList.remove('scale-100', 'opacity-100');
    setTimeout(() => {
        modal.classList.add('hidden');
    }, 300);
}

function setAuthRole(role) {
    currentAuthRole = role;
    ['student', 'vendor', 'delivery'].forEach(r => {
        const pill = document.getElementById(`auth-pill-${r}`);
        if(pill) {
            if (r === role) {
                pill.classList.add('bg-white', 'text-igntu-navy', 'shadow-sm');
                pill.classList.remove('text-slate-400');
            } else {
                pill.classList.remove('bg-white', 'text-igntu-navy', 'shadow-sm');
                pill.classList.add('text-slate-400');
            }
        }
    });
}

async function handleAuthAction(e) {
    if (e) e.preventDefault();
    
    const nameInput = document.getElementById('auth-name');
    const phoneInput = document.getElementById('auth-phone');
    const emailInput = document.getElementById('auth-email');
    
    const name = nameInput ? nameInput.value.trim() : '';
    const phone = phoneInput ? phoneInput.value.trim() : '';
    const email = emailInput ? emailInput.value.trim() : '';
    
    const rolePill = currentAuthRole;
    let selectedRole = rolePill;
    if (selectedRole === 'delivery') selectedRole = 'partner';
    
    const otpContainer = document.getElementById('otp-input-container');
    const otpInput = document.getElementById('auth-otp');
    const btnText = document.getElementById('auth-btn-text');
    const btnIcon = document.getElementById('auth-btn-icon');
    const btn = document.getElementById('btn-auth-action');
    
    // Check if we need to request the OTP code first
    if (otpContainer.classList.contains('hidden')) {
        if (!name) return showToast("Please enter your name!", "error");
        if (!phone || phone.length !== 10) return showToast("Please enter a valid 10-digit mobile number!", "error");
        if (!email) return showToast("Please enter your email address!", "error");
        
        const originalText = btnText.innerText;
        btnText.innerText = "Sending Code...";
        btnIcon.className = "fa-solid fa-spinner fa-spin text-lg text-[#FFB703]";
        btn.disabled = true;
        
        try {
            const res = await fetch(`${API_URL}/auth/request-otp`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email: email, selected_role: selectedRole })
            });
            const data = await res.json();
            
            if (res.ok && data.status === 'success') {
                otpContainer.classList.remove('hidden');
                
                // Hide input containers for cleaner UI when entering OTP
                const nameCont = document.getElementById('name-input-container');
                const phoneCont = document.getElementById('phone-input-container');
                const emailCont = document.getElementById('email-input-container');
                if (nameCont) nameCont.classList.add('hidden');
                if (phoneCont) phoneCont.classList.add('hidden');
                if (emailCont) emailCont.classList.add('hidden');
                
                btnText.innerText = "Verify & Login";
                btnIcon.className = "fa-solid fa-check text-lg text-[#FFB703]";
                
                // If EmailJS is configured, send the OTP via EmailJS
                if (typeof emailjs !== 'undefined' && PUBLIC_KEY && SERVICE_ID && TEMPLATE_ID) {
                    const templateParams = {
                        to_email: email,
                        to_name: name,
                        otp_code: data.otp,
                        otp: data.otp,
                        verification_code: data.otp,
                        message: `Your Mekal Mart verification code is ${data.otp}`
                    };
                    emailjs.send(SERVICE_ID, TEMPLATE_ID, templateParams)
                        .then(() => {
                            showToast("Verification code sent to your email via EmailJS!", "success");
                        }, (err) => {
                            console.error("EmailJS OTP Send Error:", err);
                            showToast("EmailJS failed to send. Developer fallback: enter " + data.otp, "warning");
                            otpInput.value = data.otp;
                        });
                } else {
                    // Fallback to auto-fill if EmailJS is not configured
                    otpInput.value = data.otp;
                    showToast("EmailJS not configured. Auto-filled code " + data.otp, "info");
                }
            } else {
                showToast(data.detail || data.message || "Failed to send code", "error");
                btnText.innerText = originalText;
                btnIcon.className = "fa-solid fa-paper-plane text-lg text-[#FFB703]";
            }
        } catch (err) {
            showToast("Connection error. Please try again.", "error");
            btnText.innerText = originalText;
            btnIcon.className = "fa-solid fa-paper-plane text-lg text-[#FFB703]";
        } finally {
            btn.disabled = false;
        }
    } else {
        // Verify OTP and Login
        const otpVal = otpInput.value.trim();
        if (otpVal.length !== 6) return showToast("Enter the 6-digit verification code!", "error");
        
        const originalText = btnText.innerText;
        btnText.innerText = "Verifying...";
        btnIcon.className = "fa-solid fa-spinner fa-spin text-lg text-[#FFB703]";
        btn.disabled = true;
        
        try {
            const res = await fetch(`${API_URL}/auth/login`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email: email, otp: otpVal, selected_role: selectedRole, name: name, phone: phone })
            });
            const data = await res.json();
            
            if (res.ok && data.status === 'success') {
                localStorage.setItem('uniMartToken', data.token);
                localStorage.setItem('uniMartActiveUser', JSON.stringify(data.user));
                localStorage.setItem('uniActiveRole', data.user.role);
                
                currentUser = data.user;
                showToast("Signed in as " + currentUser.name, "success");
                initSession(currentUser);
                
                setTimeout(() => {
                    if (currentUser.role === 'admin') {
                        window.location.href = 'admin_dashboard.html';
                    } else if (currentUser.role === 'vendor') {
                        window.location.href = 'vendor_dashboard.html';
                    } else if (currentUser.role === 'partner' || currentUser.role === 'delivery') {
                        window.location.href = 'delivery_dashboard.html';
                    } else {
                        window.location.href = 'index.html';
                    }
                }, 1000);
            } else {
                showToast(data.detail || data.message || "Login failed", "error");
                btnText.innerText = originalText;
                btnIcon.className = "fa-solid fa-check text-lg text-[#FFB703]";
            }
        } catch (err) {
            showToast("Connection error. Please try again.", "error");
            btnText.innerText = originalText;
            btnIcon.className = "fa-solid fa-check text-lg text-[#FFB703]";
        } finally {
            btn.disabled = false;
        }
    }
}

async function triggerMockLogin() {
    const email = prompt("Enter email for Mock login (Dev Bypass):", "student@example.com");
    if (!email) return;
    
    let selectedRole = localStorage.getItem('selectedRoleForGoogleAuth') || currentAuthRole || 'student';
    if (selectedRole === 'delivery') selectedRole = 'partner';
    
    const nameVal = document.getElementById('auth-name') ? document.getElementById('auth-name').value.trim() : '';
    const phoneVal = document.getElementById('auth-phone') ? document.getElementById('auth-phone').value.trim() : '';
    
    try {
        const res = await fetch(`${API_URL}/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                email: email,
                otp: "123456",
                selected_role: selectedRole,
                name: nameVal || email.split('@')[0],
                phone: phoneVal || "9999999999"
            })
        });
        const data = await res.json();
        if (res.ok && data.status === 'success') {
            localStorage.setItem('uniMartToken', data.token);
            localStorage.setItem('uniMartActiveUser', JSON.stringify(data.user));
            localStorage.setItem('uniActiveRole', data.user.role);
            localStorage.removeItem('selectedRoleForGoogleAuth');
            
            currentUser = data.user;
            showToast("Mock Signed in as " + currentUser.name);
            initSession(currentUser);
            
            setTimeout(() => {
                if (currentUser.role === 'admin') {
                    window.location.href = 'admin_dashboard.html';
                } else if (currentUser.role === 'vendor') {
                    window.location.href = 'vendor_dashboard.html';
                } else if (currentUser.role === 'partner' || currentUser.role === 'delivery') {
                    window.location.href = 'delivery_dashboard.html';
                } else {
                    window.location.href = 'index.html';
                }
            }, 1000);
        } else {
            showToast(data.detail || "Mock registration failed", "error");
        }
    } catch (e) {
        showToast("Sync Error: " + e.message, "error");
    }
}

// --- SESSION ---
function initSession(user) {
    closeAuthModal();
    updateNavUI();
    if (document.getElementById('checkout-name')) {
        document.getElementById('checkout-name').value = user.name || '';
        document.getElementById('checkout-email').value = user.email || '';
        document.getElementById('checkout-phone').value = user.phone || '';
    }
    if (products.length > 0) fetchAIRecommendations();
}

async function logout() {
    localStorage.removeItem('uniMartActiveUser');
    localStorage.removeItem('uniMartToken');
    if (supabaseClient) {
        try {
            await supabaseClient.auth.signOut();
        } catch(e) {}
    }
    window.location.href = 'index.html';
}

function updateNavUI() {
    const loggedInDiv = document.getElementById('nav-logged-in');
    const loggedOutDiv = document.getElementById('nav-logged-out');
    const dashboardBtn = document.getElementById('nav-dashboard-btn');
    const roleBtn = document.getElementById('nav-role-switcher');

    if (currentUser) {
        if (loggedInDiv) { loggedInDiv.classList.remove('hidden'); loggedInDiv.classList.add('flex'); }
        if (loggedOutDiv) { loggedOutDiv.classList.add('hidden'); loggedOutDiv.classList.remove('flex'); }
        
        const usernameEl = document.getElementById('nav-username');
        if (usernameEl && currentUser.name) {
            usernameEl.innerText = currentUser.name.split(' ')[0];
        }

        if (currentUser.role === 'vendor') {
            if (dashboardBtn) {
                dashboardBtn.classList.remove('hidden');
                dashboardBtn.onclick = () => { window.location.href = 'vendor_dashboard.html'; };
            }
            if (roleBtn) roleBtn.classList.add('hidden');
        } else if (currentUser.role === 'partner' || currentUser.role === 'delivery') {
            if (dashboardBtn) {
                dashboardBtn.classList.remove('hidden');
                dashboardBtn.onclick = () => { window.location.href = 'delivery_dashboard.html'; };
            }
            if (roleBtn) {
                roleBtn.classList.remove('hidden');
                const switchText = document.getElementById('nav-role-switcher-text');
                if (switchText) switchText.innerText = 'Switch to Shopping';
            }
        } else if (currentUser.role === 'admin') {
            if (dashboardBtn) {
                dashboardBtn.classList.remove('hidden');
                dashboardBtn.onclick = () => { window.location.href = 'admin_dashboard.html'; };
            }
            if (roleBtn) roleBtn.classList.add('hidden');
        } else {
            // Student role: remove all other buttons except cart, account, logout
            if (dashboardBtn) dashboardBtn.classList.add('hidden');
            if (roleBtn) roleBtn.classList.add('hidden');
        }
    } else {
        if (loggedInDiv) { loggedInDiv.classList.add('hidden'); loggedInDiv.classList.remove('flex'); }
        if (loggedOutDiv) { loggedOutDiv.classList.remove('hidden'); loggedOutDiv.classList.add('flex'); }
    }
}

async function switchUserRole() {
    if (!currentUser) return;
    const targetRole = (currentUser.role === 'partner' || currentUser.role === 'delivery') ? 'CUSTOMER' : 'AGENT';
    
    const btnText = document.getElementById('nav-role-switcher-text');
    if(btnText) btnText.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Switching...';
    
    try {
        const res = await authFetch(`${API_URL}/auth/switch-role`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email: currentUser.email, target_role: targetRole })
        });
        const data = await res.json();
        if (res.ok && data.status === 'success') {
            currentUser = data.user;
            localStorage.setItem('uniMartActiveUser', JSON.stringify(currentUser));
            localStorage.setItem('uniMartToken', data.token);
            updateNavUI();
            if (currentUser.role === 'partner' || currentUser.role === 'delivery') {
                window.location.href = 'delivery_dashboard.html';
            } else {
                window.location.href = 'index.html';
            }
        } else {
            showToast(data.message || data.detail || "Error switching role", "error");
            updateNavUI();
            if (data.message === "You must apply to be a Delivery Agent first") {
                setTimeout(() => openApplyDeliveryModal(), 1500);
            }
        }
    } catch (e) {
        showToast("Error switching role", "error");
        updateNavUI();
    }
}

function openApplyDeliveryModal() {
    const modal = document.getElementById('apply-delivery-modal');
    const card = document.getElementById('apply-delivery-card');
    modal.classList.remove('hidden');
    setTimeout(() => {
        card.classList.remove('scale-95', 'opacity-0');
        card.classList.add('scale-100', 'opacity-100');
    }, 10);
}

function closeApplyDeliveryModal() {
    const modal = document.getElementById('apply-delivery-modal');
    const card = document.getElementById('apply-delivery-card');
    card.classList.add('scale-95', 'opacity-0');
    card.classList.remove('scale-100', 'opacity-100');
    setTimeout(() => {
        modal.classList.add('hidden');
    }, 300);
}

async function submitDeliveryApplication() {
    if (!currentUser) return;
    
    const studentId = document.getElementById('apply-student-id').value;
    const hostel = document.getElementById('apply-hostel').value;
    const vehicle = document.getElementById('apply-vehicle').value;
    
    if (!studentId || !hostel) return showToast("Student ID and Hostel are required", "error");
    
    const btn = document.getElementById('btn-apply-delivery');
    const originalText = btn.innerHTML;
    btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Submitting...`;
    btn.disabled = true;
    
    try {
        const res = await authFetch(`${API_URL}/auth/apply-delivery`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                email: currentUser.email,
                student_id: studentId,
                hostel: hostel,
                vehicle_number: vehicle
            })
        });
        const data = await res.json();
        
        if (res.status === 404) {
            return showToast("Endpoint not found. Please restart your Python server!", "error");
        }
        if (res.status === 422) {
            return showToast("Data validation error. Please check your inputs.", "error");
        }
        
        if (res.ok && data.status === 'success') {
            showToast(data.message, "success");
            currentUser = data.user;
            localStorage.setItem('uniMartActiveUser', JSON.stringify(currentUser));
            localStorage.setItem('uniMartToken', data.token);
            closeApplyDeliveryModal();
            setTimeout(() => {
                window.location.href = 'delivery_dashboard.html';
            }, 1000);
        } else {
            let errMsg = data.message || "Failed to submit application";
            if (!data.message && data.detail) {
                errMsg = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
            }
            showToast("Error: " + errMsg, "error");
        }
    } catch (e) {
        showToast("Connection failed", "error");
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}

// ============================================================
// 4. PROFILE & HISTORY
// ============================================================
async function openProfile() {
    if (!currentUser) return;
    document.getElementById('profile-modal').classList.remove('hidden');
    document.getElementById('profile-name').innerText = currentUser.name;
    document.getElementById('profile-email').innerText = currentUser.email;
    document.getElementById('profile-phone').innerText = currentUser.phone || 'Not provided';
    loadWalletBalance();

    if (currentUser.image) {
        document.getElementById('profile-img-preview').src = currentUser.image + '?t=' + new Date().getTime();
        document.getElementById('profile-img-preview').classList.remove('hidden');
        document.getElementById('profile-img-icon').classList.add('hidden');
    }

    const list = document.getElementById('order-history-list');
    list.innerHTML = '<p class="text-center py-4"><i class="fa-solid fa-spinner fa-spin"></i> Loading history...</p>';

    try {
        const response = await authFetch(`${API_URL}/orders/history?phone=${currentUser.phone || currentUser.email}`);
        const orders = await response.json();

        list.innerHTML = '';
        if (orders.length === 0) {
            list.innerHTML = '<p class="text-center text-gray-400 py-4">No previous orders.</p>';
        } else {
            orders.forEach(order => {
                list.innerHTML += `
                    <div class="bg-white p-4 rounded-lg border border-gray-200 shadow-sm mb-3">
                        <div class="flex justify-between mb-2">
                            <span class="font-bold text-secondary font-montserrat">#${order.id}</span>
                            <span class="text-xs text-gray-500">${order.date}</span>
                        </div>
                        <div class="text-sm text-gray-700 mb-2 whitespace-pre-line">${order.items}</div>
                        <div class="flex justify-between items-center border-t pt-2">
                            <span class="text-xs font-bold text-gray-500">${order.hostel}</span>
                            <span class="font-bold text-green-600 text-lg">₹${order.total}</span>
                        </div>
                    </div>`;
            });
        }
    } catch (e) {
        list.innerHTML = '<p class="text-center text-red-500 py-4">Failed to load history.</p>';
    }
}


async function loadWalletBalance() {
    try {
        const res = await authFetch(`${API_URL}/wallet/balance?phone=${currentUser.phone || currentUser.email}`);
        const data = await res.json();
        if (data.status === 'success') {
            document.getElementById('profile-wallet-balance').innerText = `₹${parseFloat(data.balance).toFixed(2)}`;
        }
    } catch (e) { }
}

async function addMoneyToWallet() {
    const amountInput = document.getElementById('add-wallet-amount');
    const amount = amountInput ? amountInput.value : 0;
    
    if (!amount || amount <= 0) return showToast("Enter a valid amount.", "error");

    const btn = event.currentTarget;
    const originalText = btn.innerHTML;
    btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Initializing...`;
    btn.disabled = true;

    try {
        const orderRes = await authFetch(`${API_URL}/wallet/create-order`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ amount: parseFloat(amount) })
        });
        const orderData = await orderRes.json();
        
        if (!orderRes.ok || orderData.status !== 'success') {
            throw new Error(orderData.detail || "Failed to initialize payment order");
        }
        
        if (typeof Razorpay === 'undefined') {
            throw new Error("Payment gateway is loading. Please try again in a moment.");
        }

        const options = {
            "key": orderData.key_id, 
            "amount": amount * 100, 
            "currency": "INR",
            "name": "Mekal Mart Wallet",
            "description": "Add money to your campus wallet",
            "order_id": orderData.order_id,
            "handler": async function (response) {
                console.log("Razorpay Success:", response);
                try {
                    const res = await authFetch(`${API_URL}/wallet/add`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            razorpay_payment_id: response.razorpay_payment_id,
                            razorpay_order_id: response.razorpay_order_id || orderData.order_id,
                            razorpay_signature: response.razorpay_signature || "mock_signature",
                            amount: parseFloat(amount),
                            phone: currentUser.phone || currentUser.email
                        })
                    });
                    const result = await res.json();
                    if (res.ok && result.status === 'success') {
                        showToast("Wallet updated successfully!");
                        loadWalletBalance();
                        if(amountInput) amountInput.value = '';
                    } else {
                        showToast(result.detail || result.message || "Verification failed", "error");
                    }
                } catch (e) {
                    console.error("Wallet Verification Error:", e);
                    showToast("Verification failed", "error");
                }
            },
            "prefill": {
                "name": currentUser ? currentUser.name : '',
                "email": currentUser ? currentUser.email : '',
                "contact": currentUser ? currentUser.phone : ''
            },
            "theme": { "color": "#1B4332" },
            "modal": {
                "ondismiss": function() { console.log("Payment modal closed"); }
            }
        };
        const rzp1 = new Razorpay(options);
        rzp1.open();
    } catch (err) {
        showToast(err.message || "Failed to initiate payment", "error");
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}


function closeProfile() { document.getElementById('profile-modal').classList.add('hidden'); }
function deleteAccount() { alert("Contact Admin to delete database record."); }

// ============================================================
// 5. GOOGLE MAPS
// ============================================================
function detectLocation() {
    if (navigator.geolocation) {
        const btn = event.currentTarget;
        const originalText = btn.innerHTML;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Locating...';
        navigator.geolocation.getCurrentPosition(
            (position) => {
                const pos = { lat: position.coords.latitude, lng: position.coords.longitude };
                initMap(pos);
                btn.innerHTML = originalText;
            },
            (error) => { alert("Error: " + error.message); btn.innerHTML = originalText; }
        );
    } else { alert("Geolocation not supported."); }
}

function initMap(pos) {
    document.getElementById('google-map').style.display = 'block';
    map = new google.maps.Map(document.getElementById("google-map"), { zoom: 15, center: pos, disableDefaultUI: true });
    marker = new google.maps.Marker({ position: pos, map: map, draggable: true, animation: google.maps.Animation.DROP });
    geocoder = new google.maps.Geocoder();
    geocodePosition(pos);
    google.maps.event.addListener(marker, 'dragend', function () { geocodePosition(marker.getPosition()); });
}

function geocodePosition(pos) {
    geocoder.geocode({ location: pos }, (results, status) => {
        if (status === "OK" && results[0]) {
            document.getElementById('checkout-address').value = results[0].formatted_address;
        }
    });
}

// ============================================================
// 6. RENDER SHOPS & PRODUCTS
// ============================================================
let shops = [];

async function renderShops() {
    const list = document.getElementById('shop-list');
    list.innerHTML = `<div onclick="filterByShop('all')" class="shop-item flex flex-col items-center justify-center p-2 rounded-xl cursor-pointer hover:bg-gray-100 transition h-[80px] min-w-[80px] md:h-20 ${activeShopId === 'all' ? 'ring-2 ring-secondary bg-secondary/10' : ''}">
                        <div class="w-10 h-10 rounded-full bg-secondary/10 text-secondary flex items-center justify-center text-lg mb-1"><i class="fa-solid fa-list"></i></div>
                        <span class="text-xs font-bold text-gray-700">All</span>
                      </div>`;

    try {
        const response = await fetch(`${API_URL}/shops`);
        const data = await response.json();

        shops = data;

        shops.forEach(shop => {
            const isActive = activeShopId === shop.id ? 'ring-2 ring-secondary bg-secondary/10' : '';
            list.innerHTML += `
                <div onclick="filterByShop('${shop.id}')" class="shop-item flex flex-col items-center justify-center p-2 rounded-xl cursor-pointer hover:bg-gray-100 transition h-[80px] min-w-[80px] md:h-20 ${isActive}" title="${shop.name}">
                    <div class="w-10 h-10 rounded-full bg-gray-200 border-2 border-white shadow-sm overflow-hidden mb-1 flex items-center justify-center">
                        <i class="fa-solid fa-store text-gray-400"></i>
                    </div>
                    <span class="text-xs font-bold text-gray-700 truncate w-full text-center px-1">${shop.name}</span>
                </div>
            `;
        });
    } catch (error) {
        console.error("Failed to load shops", error);
    }
}

function getAddButtonHTML(productId, inStock) {
    if (!inStock) {
        return `<button disabled class="bg-slate-100 text-slate-400 cursor-not-allowed px-3 py-1.5 rounded-lg text-xs font-montserrat font-bold shadow-sm border border-slate-200 whitespace-nowrap">Out</button>`;
    }
    const cartItem = cart.find(item => item.id === productId);
    if (cartItem) {
        return `
        <div class="flex items-center justify-between bg-secondary/10 text-secondary border border-secondary/20 rounded-lg h-[34px] px-1 shadow-sm overflow-hidden transition-all duration-300 font-montserrat">
            <button onclick="updateCartItem(${productId}, -1)" class="w-1/3 h-full flex items-center justify-center hover:bg-secondary/20 transition"><i class="fa-solid fa-minus text-[10px]"></i></button>
            <span class="text-sm font-bold w-1/3 text-center">${cartItem.quantity}</span>
            <button onclick="updateCartItem(${productId}, 1)" class="w-1/3 h-full flex items-center justify-center hover:bg-secondary/20 transition"><i class="fa-solid fa-plus text-[10px]"></i></button>
        </div>`;
    } else {
        return `<button onclick="addToCart(${productId})" class="quick-add-btn">
            <i class="fa-solid fa-plus"></i>
            <span>Add</span>
        </button>`;
    }
}

function displayItems(items) {
    const grid = document.getElementById('product-grid');
    grid.innerHTML = '';
    if (items.length === 0) return grid.innerHTML = `<div class="col-span-full flex flex-col items-center justify-center py-10 text-gray-400"><i class="fa-solid fa-box-open text-4xl mb-2"></i><p>No products found.</p></div>`;
    items.forEach(product => {
        const inStock = product.in_stock === 1;
        const safeName = sanitize(product.name);
        const safeCategory = sanitize(product.category);
        const safeShopName = sanitize(shops.find(s => s.id == product.shop_id)?.name || 'Store');

        grid.innerHTML += `
        <div class="product-info-card ${!inStock ? 'opacity-60 grayscale-[50%]' : ''}">
            ${!inStock ? '<div class="absolute inset-0 z-20 bg-white/40 backdrop-blur-[2px] flex items-center justify-center pointer-events-none"><span class="bg-gradient-to-r from-red-500 to-rose-600 text-white font-bold px-4 py-1.5 rounded-full text-xs shadow-lg border-2 border-white -rotate-12 transform scale-110">Sold Out</span></div>' : ''}
            <div class="h-40 overflow-hidden relative rounded-t-2xl z-0 bg-gray-50 flex items-center justify-center p-4">
                <img src="${product.img}" class="max-w-full max-h-full object-contain transform transition duration-500 ${inStock ? 'hover:scale-105' : ''}">
                <div class="product-badge">
                    <i class="fa-solid fa-flame text i-3"></i> Fresh
                </div>
                <div class="absolute top-2 left-2 bg-white/90 backdrop-blur-sm px-2 py-1 rounded-md border border-white/50 shadow-sm">
                    <p class="text-[8px] text-gray-600 font-bold uppercase tracking-wider truncate max-w-[100px]">${safeShopName}</p>
                </div>
            </div>
            <div class="p-3 flex flex-col flex-1 z-10 bg-white rounded-b-2xl">
                <h3 class="product-name line-clamp-2">${safeName}</h3>
                <span class="product-desc line-clamp-1">${safeCategory}</span>
                <div class="mt-auto">
                    <div class="delivery-time-badge mb-2">
                        <i class="fa-solid fa-clock"></i>
                        <span>10-15 mins</span>
                    </div>
                </div>
                <div class="product-footer">
                    <span class="text-[15px] font-black text-gray-900">₹${product.price}</span>
                    <div id="add-btn-container-${product.id}">
                        ${getAddButtonHTML(product.id, inStock)}
                    </div>
                </div>
            </div>
        </div>`;
    });
}

function filterByShop(shopId) {
    activeShopId = shopId; renderShops();
    if (shopId === 'all') displayItems(products);
    else displayItems(products.filter(p => p.shop_id == shopId));
}
function filterProducts(category) {
    let base = activeShopId === 'all' ? products : products.filter(p => p.shop_id == activeShopId);
    displayItems(category === 'all' ? base : base.filter(p => p.category === category));
}
function handleSearch(query) {
    activeShopId = 'all'; renderShops();
    displayItems(products.filter(p => p.name.toLowerCase().includes(query.toLowerCase()) || p.category.includes(query.toLowerCase())));
}

// ============================================================
// 7. CART & TOAST
// ============================================================
function addToCart(id) {
    if (!currentUser) { openAuthModal(); return; }
    const product = products.find(p => p.id === id);
    const existing = cart.find(item => item.id === id);
    if (existing) existing.quantity++; else cart.push({ ...product, quantity: 1 });

    updateCartUI();
    saveCart();

    // Update the button UI without re-rendering the whole grid
    const btnContainer = document.getElementById(`add-btn-container-${id}`);
    if (btnContainer) {
        btnContainer.innerHTML = getAddButtonHTML(id, product.in_stock === 1);
    }
}

function updateCartItem(id, change) {
    const itemIndex = cart.findIndex(i => i.id === id);
    if (itemIndex > -1) {
        cart[itemIndex].quantity += change;
        if (cart[itemIndex].quantity <= 0) {
            cart.splice(itemIndex, 1);
        }
    }
    updateCartUI();
    saveCart();
    const btnContainer = document.getElementById(`add-btn-container-${id}`);
    if (btnContainer) {
        const product = products.find(p => p.id === id);
        btnContainer.innerHTML = getAddButtonHTML(id, product ? product.in_stock === 1 : true);
    }
}

function showToast(msg, type = 'success') {
    const toast = document.getElementById('toast');
    const title = document.getElementById('toast-title');
    const message = document.getElementById('toast-message');
    const iconContainer = document.getElementById('toast-icon-container');
    const icon = document.getElementById('toast-icon');

    // Reset Classes
    toast.className = "fixed top-24 right-4 md:right-10 z-[90] bg-white border-l-4 shadow-2xl rounded-lg p-4 flex items-center gap-4 transition-all duration-500 transform translate-x-full opacity-0 pointer-events-none min-w-[300px]";
    iconContainer.className = "w-8 h-8 rounded-full flex items-center justify-center shrink-0";

    // Set Theme
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
    setTimeout(() => { toast.classList.remove('translate-x-full', 'opacity-0'); toast.classList.add('translate-x-0', 'opacity-100'); }, 10);
    if (toast.hideTimeout) clearTimeout(toast.hideTimeout);
    toast.hideTimeout = setTimeout(() => { toast.classList.remove('translate-x-0', 'opacity-100'); toast.classList.add('translate-x-full', 'opacity-0'); }, 3000);
}

function removeFromCart(id) {
    cart = cart.filter(item => item.id !== id);
    updateCartUI();
    saveCart();
    const btnContainer = document.getElementById(`add-btn-container-${id}`);
    if (btnContainer) {
        const product = products.find(p => p.id === id);
        btnContainer.innerHTML = getAddButtonHTML(id, product ? product.in_stock === 1 : true);
    }
}
function updateCartUI() {
    document.getElementById('cart-count').innerText = cart.reduce((sum, item) => sum + item.quantity, 0);
    const list = document.getElementById('cart-items');
    const total = cart.reduce((sum, item) => sum + (item.price * item.quantity), 0);
    document.getElementById('cart-total').innerText = "₹" + total.toFixed(2);
    list.innerHTML = cart.length ? cart.map(i => `<div class="flex justify-between items-center bg-white p-3 rounded-xl border border-slate-100 shadow-sm hover:shadow-md transition"><div class="flex items-center gap-3"><img src="${i.img}" class="w-12 h-12 rounded-lg object-cover shadow-sm"><div><h4 class="font-bold font-montserrat text-sm text-slate-800 line-clamp-1">${i.name}</h4><p class="text-[11px] font-medium text-slate-500">₹${i.price} <span class="text-secondary font-bold ml-1">x ${i.quantity}</span></p></div></div><button onclick="removeFromCart(${i.id})" class="w-8 h-8 rounded-full bg-red-50 text-red-500 hover:bg-red-500 hover:text-white transition flex items-center justify-center"><i class="fa-solid fa-trash-can text-xs"></i></button></div>`).join('') : '<div class="text-center py-10 opacity-50"><i class="fa-solid fa-basket-shopping text-4xl mb-3 text-slate-400"></i><p class="text-slate-500 font-medium">Cart is empty</p></div>';
}
function toggleCart() { document.getElementById('cart-sidebar').classList.toggle('hide-cart'); document.getElementById('cart-sidebar').classList.toggle('show-cart'); document.getElementById('overlay').classList.toggle('hidden'); }
function returnToShop() { document.getElementById('checkout-view').classList.add('hidden'); document.getElementById('shop-view').classList.remove('hidden'); }
function goToCheckout() {
    if (!cart.length) return showToast("Cart is empty!", "error");
    toggleCart(); document.getElementById('shop-view').classList.add('hidden'); document.getElementById('checkout-view').classList.remove('hidden');
    const total = cart.reduce((sum, item) => sum + (item.price * item.quantity), 0);
    document.getElementById('checkout-items').innerHTML = cart.map(i => `<div class="flex justify-between border-b pb-2"><span class="text-sm">${i.name} x${i.quantity}</span><span class="font-bold">₹${i.price * i.quantity}</span></div>`).join('');
    document.getElementById('summary-subtotal').innerText = "₹" + total.toFixed(2);
    const feeEl = document.getElementById('summary-delivery-fee');
    if (feeEl) feeEl.innerText = "₹" + DELIVERY_FEE.toFixed(2);
    document.getElementById('summary-total').innerText = "₹" + (total + DELIVERY_FEE).toFixed(2);
    updateQR(total + DELIVERY_FEE);
}
function updateQR(amount) {
    document.getElementById('upi-qr-image').src = `https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(`upi://pay?pa=9142232046@sbi&pn=Mekal Mart&am=${amount}&cu=INR`)}`;
    document.getElementById('qr-amount').innerText = "₹" + amount;
}

function togglePaymentUI() {
    const method = document.querySelector('input[name="payment_method"]:checked').value;
    const upiSection = document.getElementById('upi-payment-section');
    if (method === 'upi') {
        upiSection.classList.remove('hidden');
    } else {
        upiSection.classList.add('hidden');
    }
}

// ============================================================
// 8. ORDER PROCESSING (DB + EMAIL)
// ============================================================
async function placeOrder() {
    let subtotal = cart.reduce((sum, item) => sum + (item.price * item.quantity), 0);
    let totalAmount = subtotal + DELIVERY_FEE;

    const name = document.getElementById('checkout-name').value.trim();
    const phone = document.getElementById('checkout-phone').value.trim();
    const email = document.getElementById('checkout-email').value.trim();
    const hostel = document.getElementById('checkout-hostel').value;
    const room = document.getElementById('checkout-room').value.trim();

    if (!name || !phone || !email || !hostel || !room) return showToast("Fill all fields.", "error");

    const btn = event.target;
    btn.innerText = "Processing...";
    btn.disabled = true;
    btn.classList.add('opacity-75');

    const itemsSummary = cart.map(i => `${i.name} (x${i.quantity})`).join(', ');
    const method = document.querySelector('input[name="payment_method"]:checked').value;

    let orderID;
    try {
        const response = await authFetch(`${API_URL}/orders/place`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name, email, phone, hostel, room,
                items: itemsSummary,
                total: totalAmount,
                payment_method: method,
                shop_id: cart.length > 0 ? cart[0].shop_id : null
            })
        });
        const result = await response.json();

        if (!response.ok || result.status !== 'success') {
            const errMsg = result.detail || result.message || "Order placement failed";
            throw new Error(typeof errMsg === 'string' ? errMsg : JSON.stringify(errMsg));
        }
        orderID = result.order_id;
    } catch (e) {
        showToast("Order failed: " + e.message, "error");
        btn.innerText = "Confirm Payment & Order";
        btn.disabled = false;
        btn.classList.remove('opacity-75');
        return;
    }

    const orderHtml = cart.map(i => `<tr><td style="padding:8px;border-bottom:1px solid #ddd">${i.name}</td><td style="padding:8px;border-bottom:1px solid #ddd">x${i.quantity}</td><td style="padding:8px;border-bottom:1px solid #ddd">₹${i.price * i.quantity}</td></tr>`).join('');
    const emailParams = {
        to_email: email, customer_name: name, order_id: orderID,
        total_amount: "₹" + totalAmount.toFixed(2), hostel_name: hostel, room_no: room,
        phone_no: phone, message_html: orderHtml
    };

    sendOrderConfirmationEmail(emailParams, () => {
        showToast(PUBLIC_KEY && SERVICE_ID ? "Order Confirmed! Check Email." : "Order Confirmed!", "success");
        finishOrder(btn, orderID);
    });
}

function finishOrder(btn, orderID) {
    document.getElementById('checkout-phone').value = '';
    document.getElementById('checkout-room').value = '';
    cart = []; saveCart(); updateCartUI(); returnToShop(); startTracking(orderID);
    btn.innerText = "Confirm Payment & Order"; btn.disabled = false; btn.classList.remove('opacity-75');
}

// --- REAL-TIME TRACKING (POLLING DB) ---
function startTracking(orderID) {
    // Fallback if no ID passed (legacy)
    if (!orderID && currentUser) orderID = 'Unknown';
    if (!orderID) return;

    const modal = document.getElementById('tracking-modal');
    const bar = document.getElementById('track-bar');
    const timerText = document.getElementById('countdown-timer');

    modal.classList.remove('hidden');
    setTimeout(() => { modal.classList.remove('pointer-events-none', 'opacity-0'); modal.classList.add('tracking-active'); }, 10);

    timerText.innerText = "Pending...";

    // Poll Database every 5 seconds
    if (window.trackingInterval) clearInterval(window.trackingInterval);
    window.trackingInterval = setInterval(async () => {
        if (document.hidden) return; // Stop terminal spam if tab is hidden
        try {
            const res = await authFetch(`${API_URL}/orders/history?phone=${currentUser.phone || currentUser.email}`);
            const data = await res.json();
            
            // Find our specific order in history
            const order = data.find(o => o.id === orderID);
            if (order) {
                const status = order.status || 'Pending';

                // Show Agent immediately (Privacy Safeguard)
                const isActive = ['Driver Assigned', 'Dispatched', 'Out for Delivery', 'Picked Up'].includes(status);
                if (order.agent_name && isActive && order.agent_phone) {
                    document.getElementById('agent-info-container').classList.remove('hidden');
                    document.getElementById('tr-agent-name').innerText = order.agent_name;
                    document.getElementById('tr-agent-phone').href = 'tel:' + order.agent_phone;
                    document.getElementById('tr-agent-phone').classList.remove('hidden');
                    if (order.agent_image) {
                        document.getElementById('tr-agent-image').src = order.agent_image;
                        document.getElementById('tr-agent-image').classList.remove('hidden');
                        document.getElementById('tr-agent-icon').classList.add('hidden');
                    } else {
                        document.getElementById('tr-agent-image').classList.add('hidden');
                        document.getElementById('tr-agent-icon').classList.remove('hidden');
                    }
                } else {
                    // Privacy clean: Clear variables and hide DOM
                    order.agent_phone = null;
                    order.agent_name = null;
                    order.agent_image = null;
                    document.getElementById('tr-agent-name').innerText = '';
                    document.getElementById('tr-agent-phone').href = '#';
                    document.getElementById('tr-agent-phone').classList.add('hidden');
                    document.getElementById('tr-agent-image').src = '';
                    document.getElementById('tr-agent-image').classList.add('hidden');
                    document.getElementById('tr-agent-icon').classList.remove('hidden');
                    document.getElementById('agent-info-container').classList.add('hidden');
                }
                
                // Display the generated delivery_pin exclusively on the customer's dashboard tracker view
                if (order.delivery_pin) {
                    document.getElementById('tr-delivery-pin').innerText = order.delivery_pin;
                }

                if (status === 'Pending') {
                    bar.style.width = "5%";
                    timerText.innerText = "Waiting...";
                }
                else if (status === 'Accepted' || status === 'Driver Assigned') {
                    bar.style.width = "25%";
                    timerText.innerText = status === 'Driver Assigned' ? "Driver Assigned" : "Accepted";
                    highlightStep(1);
                }
                else if (status === 'Packing' || status === 'Dispatched') {
                    bar.style.width = "50%";
                    timerText.innerText = status === 'Dispatched' ? "Dispatched" : "Packing";
                    highlightStep(1); highlightStep(2);
                }
                else if (status === 'Picked Up' || status === 'Handed to Delivery Agent' || status === 'Out for Delivery') {
                    bar.style.width = "80%";
                    timerText.innerText = "Out for Delivery";
                    highlightStep(1); highlightStep(2); highlightStep(3);
                }
                else if (status === 'Delivered') {
                    bar.style.width = "100%";
                    timerText.innerText = "Arrived!";
                    highlightStep(1); highlightStep(2); highlightStep(3); highlightStep(4);
                    clearInterval(window.trackingInterval);
                }
                else if (status === 'Cancelled') {
                    bar.style.width = "0%";
                    timerText.innerText = "Cancelled";
                    clearInterval(window.trackingInterval);
                }
            }
        } catch (e) {
            console.error("Tracking poll failed", e);
        }
    }, 5000);
}
function highlightStep(num) { document.getElementById(`step-${num}`).classList.remove('opacity-50'); document.getElementById(`step-${num}`).classList.add('opacity-100', 'scale-105'); }
function closeTracking() {
    if (window.trackingInterval) clearInterval(window.trackingInterval);
    const modal = document.getElementById('tracking-modal');
    modal.classList.add('pointer-events-none', 'opacity-0');
    modal.classList.remove('tracking-active');
    setTimeout(() => { modal.classList.add('hidden'); }, 300);
}

// ============================================================
// 9. AI RECOMMENDATIONS WITH GEMINI
// ============================================================
async function fetchAIRecommendations() {
    if (!currentUser || products.length === 0) return;

    // 1. Fetch order history
    try {
        const hRes = await authFetch(`${API_URL}/orders/history?phone=${currentUser.phone || currentUser.email}`);
        const history = await hRes.json();

        const aiSection = document.getElementById('ai-recommendations');
        if (!aiSection) return; // Prevent errors if UI is hidden

        aiSection.classList.remove('hidden');
        document.getElementById('ai-loader').classList.remove('hidden');
        const grid = document.getElementById('ai-products-grid');
        grid.innerHTML = ''; // Keep empty while loading

        // 2. Build Prompt Context
        // If they have no history, mock it based on timezone.
        let pastItems = "none";
        if (history.length > 0) {
            pastItems = history.map(h => h.items).join(", ").substring(0, 500); // Limit context
        }

        const catalogInfo = products.filter(p => p.in_stock === 1).map(p => `{"id": ${p.id}, "name": "${p.name}"}`).join(", ");

        const prompt = `
        You are an AI recommendation engine for a college campus delivery system (Mekal Mart).
        User Name: ${currentUser.name}
        User's past purchases: ${pastItems}
        Current context: Mid-term exams just started, late-night studies are common.
        Catalog: [${catalogInfo}]
        
        Task: Pick exactly 4 unique product IDs from the catalog that this user would most likely buy right now.
        Return ONLY valid JSON array of numbers, no markdown, no text. Example: [1, 5, 12, 8]
        `;

        // 3. Make Gemini API Call via Server-Side Proxy (key is safe on server)
        let recommendedIds = [];

        try {
            const apiRes = await authFetch(`${API_URL}/ai/recommendations`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ prompt: prompt })
            });

            const aiData = await apiRes.json();
            if (aiData.error) throw new Error(aiData.error);
            const aiTextResponse = aiData.candidates[0].content.parts[0].text;
            recommendedIds = JSON.parse(aiTextResponse);
            document.getElementById('ai-context-text').innerText = `Handpicked for ${sanitize(currentUser.name)} considering mid-terms!`;
        } catch (geminiErr) {
            console.error("Gemini proxy failed:", geminiErr);
            document.getElementById('ai-context-text').innerText = "AI is resting. Here are some quick picks!";
            // Fallback: pick random in-stock items
            const inStockProds = products.filter(p => p.in_stock === 1);
            const shuffled = inStockProds.sort(() => 0.5 - Math.random());
            recommendedIds = shuffled.slice(0, 4).map(p => p.id);
        }

        // 4. Render recommendations
        document.getElementById('ai-loader').classList.add('hidden');

        recommendedIds.forEach(id => {
            const product = products.find(p => p.id === parseInt(id));
            if (!product || product.in_stock === 0) return;

            grid.innerHTML += `
            <div class="min-w-[140px] md:min-w-[180px] bg-white rounded-xl shadow-md overflow-hidden flex flex-col group flex-shrink-0 border border-purple-100">
                <div class="h-28 relative overflow-hidden">
                    <img src="${product.img}" class="w-full h-full object-cover transform group-hover:scale-110 transition duration-500">
                    <div class="absolute top-0 right-0 bg-gradient-to-l from-black/50 p-2">
                        <i class="fa-solid fa-sparkles text-yellow-300 text-xs"></i>
                    </div>
                </div>
                <div class="p-3 flex flex-col flex-1">
                    <h4 class="text-xs md:text-sm font-bold text-gray-800 line-clamp-1 mb-2">${product.name}</h4>
                    <div class="mt-auto flex justify-between items-center">
                        <span class="text-sm font-black text-gray-900">₹${product.price}</span>
                        <button onclick="addToCart(${product.id})" class="bg-secondary/10 text-secondary w-6 h-6 md:w-8 md:h-8 rounded-full flex items-center justify-center hover:bg-secondary hover:text-white transition shadow-sm">
                            <i class="fa-solid fa-plus text-[10px]"></i>
                        </button>
                    </div>
                </div>
            </div>`;
        });

    } catch (e) {
        console.error("History/AI Error:", e);
    }
}

async function uploadProfileImage(event) {
    if (!currentUser) return;
    const file = event.target.files[0];
    if (!file) return;

    const btnIcon = document.querySelector('label[for="profile-upload"] i');
    const ogClass = btnIcon.className;
    btnIcon.className = "fa-solid fa-spinner fa-spin text-xs";

    const formData = new FormData();
    formData.append('role', currentUser.role);
    formData.append('user_id', currentUser.id);
    formData.append('image', file);

    try {
        const res = await authFetch(`${API_URL}/profile/upload`, { method: 'POST', body: formData });
        const data = await res.json();

        btnIcon.className = ogClass;

        if (data.status === 'success') {
            document.getElementById('profile-img-preview').src = data.image_url + '?t=' + new Date().getTime();
            document.getElementById('profile-img-preview').classList.remove('hidden');
            document.getElementById('profile-img-icon').classList.add('hidden');
            currentUser.image = data.image_url;
            localStorage.setItem('uniMartActiveUser', JSON.stringify(currentUser));
            showToast("Profile image updated!", "success");
        } else {
            showToast(data.message, "error");
        }
    } catch (e) {
        btnIcon.className = ogClass;
        showToast("Upload failed", "error");
    }
}