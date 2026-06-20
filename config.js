// Shared Mekal Mart configuration — loaded before other scripts
const API_URL = (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1' || window.location.protocol === 'file:')
    ? 'http://localhost:8000'
    : 'https://unimart.onrender.com';

const DELIVERY_FEE = 10;

// Supabase Configuration for passwordless authentication
const SUPABASE_URL = 'https://jfiiiybykngrogajtmhm.supabase.co';
const SUPABASE_ANON_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImpmaWlpeWJ5a25ncm9nYWp0bWhtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODExMDMyMzYsImV4cCI6MjA5NjY3OTIzNn0.MMl2oNnhJPLCELSvzjwyJLg2OBkSe3e5dSFw_2KxnGs';

// EmailJS — fill in to enable order confirmation emails
const PUBLIC_KEY = 'a-TE3do_YWourmdiu';
const SERVICE_ID = 'service_12pgxll';
const TEMPLATE_ID = 'template_q8uhv3k';

// Razorpay — loaded dynamically from the backend during transaction
let RAZORPAY_KEY = '';
