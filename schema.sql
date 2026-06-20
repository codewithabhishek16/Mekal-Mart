-- Mekal Mart Centralized Authentication Schema (PostgreSQL)

-- 1. Main Users Table
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    phone VARCHAR(20) UNIQUE NULL,
    role VARCHAR(20) NOT NULL CHECK (role IN ('student', 'vendor', 'partner')),
    auth_provider VARCHAR(20) DEFAULT 'google',
    wallet_balance DECIMAL(10,2) DEFAULT 0.00,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Vendor Profiles
CREATE TABLE IF NOT EXISTS vendor_profiles (
    user_id INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    shop_name VARCHAR(255) NOT NULL,
    shop_category VARCHAR(100),
    shop_image VARCHAR(255) DEFAULT 'store-placeholder.png',
    is_approved BOOLEAN DEFAULT FALSE
);

-- 3. Student Profiles
CREATE TABLE IF NOT EXISTS student_profiles (
    user_id INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    student_name VARCHAR(255),
    university_id VARCHAR(50),
    hostel_name VARCHAR(100),
    room_number VARCHAR(20),
    profile_image VARCHAR(255) DEFAULT 'logo.png'
);

-- 4. Delivery Partner Profiles
CREATE TABLE IF NOT EXISTS partner_profiles (
    user_id INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    partner_name VARCHAR(255),
    vehicle_number VARCHAR(50),
    status VARCHAR(20) DEFAULT 'Pending Approval' CHECK (status IN ('Available', 'Busy', 'Offline', 'Pending Approval')),
    rating DECIMAL(3,2) DEFAULT 5.0,
    profile_image VARCHAR(255) DEFAULT 'logo.png'
);

-- Indexing for performance
CREATE INDEX IF NOT EXISTS idx_user_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_user_email ON users(email);

-- 5. Products Table
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    shop_id VARCHAR(255) NOT NULL,
    name VARCHAR(255) NOT NULL,
    category VARCHAR(100),
    price DECIMAL(10,2) NOT NULL,
    img VARCHAR(255) DEFAULT 'store-placeholder.png',
    stock_count INT DEFAULT 50,
    in_stock SMALLINT DEFAULT 1,
    approval_status VARCHAR(20) DEFAULT 'Pending' CHECK (approval_status IN ('Pending', 'Approved', 'Rejected')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 6. Orders Table
CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    order_id VARCHAR(50) UNIQUE NOT NULL,
    customer_name VARCHAR(255),
    phone VARCHAR(20),
    email VARCHAR(255),
    hostel VARCHAR(255),
    room_no VARCHAR(50),
    items TEXT,
    total_amount VARCHAR(50),
    delivery_fee DECIMAL(10,2) DEFAULT 0,
    status VARCHAR(50) DEFAULT 'Pending',
    delivery_pin VARCHAR(4),
    pickup_pin VARCHAR(4),
    agent_id INT,
    delivery_partner_id INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 7. Notifications Table
CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    role VARCHAR(20) NOT NULL,
    user_id VARCHAR(50) NOT NULL,
    title VARCHAR(255),
    message TEXT,
    is_read SMALLINT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 8. Wallet Transactions
CREATE TABLE IF NOT EXISTS wallet_transactions (
    id SERIAL PRIMARY KEY,
    user_id INT,
    amount DECIMAL(10,2),
    type VARCHAR(20),
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
