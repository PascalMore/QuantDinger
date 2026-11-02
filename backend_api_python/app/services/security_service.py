"""
Security Service - Handles Turnstile verification, rate limiting, and brute-force protection.
"""
import os
import json
import hashlib
import requests
from typing import Tuple, Dict, Any
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from app.config.settings import Config
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Singleton instance
_security_service = None

_TURNSTILE_PLACEHOLDER_VALUES = {
    '',
    '0',
    'false',
    'none',
    'null',
    'test',
    'dummy',
    'placeholder',
    'changeme',
    'change-me',
    'quantdinger',
    'your-turnstile-site-key',
    'your-turnstile-secret-key',
}


def _is_placeholder_turnstile_value(value: str) -> bool:
    """Return True for local/demo placeholders that should not enable Turnstile."""
    normalized = (value or '').strip().lower()
    return normalized in _TURNSTILE_PLACEHOLDER_VALUES


def get_security_service():
    """Get singleton SecurityService instance"""
    global _security_service
    if _security_service is None:
        _security_service = SecurityService()
    return _security_service


class SecurityService:
    """Security service for authentication protection"""
    
    def __init__(self):
        self._load_config()
    
    def _load_config(self):
        """Load security configuration from environment variables"""
        # Turnstile config
        self.turnstile_site_key = (os.getenv('TURNSTILE_SITE_KEY', '') or '').strip()
        self.turnstile_secret_key = (os.getenv('TURNSTILE_SECRET_KEY', '') or '').strip()
        self.turnstile_enabled = bool(
            self.turnstile_site_key
            and self.turnstile_secret_key
            and not _is_placeholder_turnstile_value(self.turnstile_site_key)
            and not _is_placeholder_turnstile_value(self.turnstile_secret_key)
        )
        if (self.turnstile_site_key or self.turnstile_secret_key) and not self.turnstile_enabled:
            logger.warning(
                "Turnstile is disabled because its site key or secret key is empty/a placeholder."
            )
        
        # IP rate limit config
        self.ip_max_attempts = int(os.getenv('SECURITY_IP_MAX_ATTEMPTS', '10'))
        self.ip_window_minutes = int(os.getenv('SECURITY_IP_WINDOW_MINUTES', '5'))
        self.ip_block_minutes = int(os.getenv('SECURITY_IP_BLOCK_MINUTES', '15'))
        
        # Account rate limit config
        self.account_max_attempts = int(os.getenv('SECURITY_ACCOUNT_MAX_ATTEMPTS', '5'))
        self.account_window_minutes = int(os.getenv('SECURITY_ACCOUNT_WINDOW_MINUTES', '60'))
        self.account_block_minutes = int(os.getenv('SECURITY_ACCOUNT_BLOCK_MINUTES', '30'))
        
        # Verification code rate limit
        self.code_rate_limit_seconds = int(os.getenv('VERIFICATION_CODE_RATE_LIMIT', '60'))
        self.code_ip_hourly_limit = int(os.getenv('VERIFICATION_CODE_IP_HOURLY_LIMIT', '10'))
        self.turnstile_clearance_ttl_seconds = int(os.getenv('TURNSTILE_CLEARANCE_TTL_SECONDS', '600'))
        self._turnstile_clearance_serializer = URLSafeTimedSerializer(
            Config.SECRET_KEY,
            salt='turnstile-clearance-v1',
        )
    
    def get_security_config(self) -> Dict[str, Any]:
        """Get public security config for frontend"""
        mobile_ver = (os.getenv('MOBILE_APP_LATEST_VERSION') or '').strip()
        mobile_url = (os.getenv('MOBILE_APP_DOWNLOAD_URL') or '').strip() or 'https://www.quantdinger.com/download/app.apk'
        return {
            'turnstile_enabled': self.turnstile_enabled,
            'turnstile_site_key': self.turnstile_site_key,
            'registration_enabled': os.getenv('ENABLE_REGISTRATION', 'true').lower() == 'true',
            'mfa_enabled': os.getenv('MFA_ENABLED', 'false').lower() == 'true',
            'mfa_risk_login_only': os.getenv('MFA_RISK_LOGIN_ONLY', 'true').lower() == 'true',
            'oauth_google_enabled': bool(os.getenv('GOOGLE_CLIENT_ID', '')),
            'oauth_github_enabled': bool(os.getenv('GITHUB_CLIENT_ID', '')),
            # Mobile in-app version check (semver-ish string, e.g. 1.0.1)
            'mobile_app_latest_version': mobile_ver,
            'mobile_app_download_url': mobile_url,
        }
    
    # =========================================================================
    # Turnstile Verification
    # =========================================================================
    
    def verify_turnstile(self, token: str, ip_address: str = None) -> Tuple[bool, str]:
        """
        Verify Cloudflare Turnstile token.
        
        Returns:
            (success, message)
        """
        if not self.turnstile_enabled:
            # If Turnstile is not configured, skip verification
            return True, 'turnstile_disabled'
        
        if not token:
            return False, 'Missing Turnstile token'
        
        try:
            response = requests.post(
                'https://challenges.cloudflare.com/turnstile/v0/siteverify',
                data={
                    'secret': self.turnstile_secret_key,
                    'response': token,
                    'remoteip': ip_address
                },
                timeout=10
            )
            result = response.json()
            
            if result.get('success'):
                return True, 'verified'
            else:
                error_codes = result.get('error-codes', [])
                logger.warning(f"Turnstile verification failed: {error_codes}")
                return False, 'Turnstile verification failed'
                
        except requests.RequestException as e:
            logger.error(f"Turnstile API error: {e}")
            # On API error, we might want to allow (fail-open) or deny (fail-closed)
            # For security, we'll deny
            return False, 'Turnstile service unavailable'

    def _turnstile_ip_hash(self, ip_address: str = None) -> str:
        ip = str(ip_address or '').strip()
        return hashlib.sha256(ip.encode('utf-8')).hexdigest()

    def issue_turnstile_clearance(self, ip_address: str = None) -> str:
        """Issue a short-lived local clearance after a valid Turnstile challenge."""
        payload = {
            'ip': self._turnstile_ip_hash(ip_address),
            'purpose': 'auth',
        }
        return self._turnstile_clearance_serializer.dumps(payload)

    def verify_turnstile_clearance(self, clearance: str, ip_address: str = None) -> Tuple[bool, str]:
        """Verify the short-lived local clearance token."""
        if not self.turnstile_enabled:
            return True, 'turnstile_disabled'
        if not clearance:
            return False, 'missing_clearance'
        try:
            payload = self._turnstile_clearance_serializer.loads(
                clearance,
                max_age=max(1, self.turnstile_clearance_ttl_seconds),
            )
        except SignatureExpired:
            return False, 'turnstile_clearance_expired'
        except BadSignature:
            return False, 'turnstile_clearance_invalid'
        if not isinstance(payload, dict) or payload.get('purpose') != 'auth':
            return False, 'turnstile_clearance_invalid'
        if payload.get('ip') != self._turnstile_ip_hash(ip_address):
            return False, 'turnstile_clearance_invalid'
        return True, 'verified'

    def verify_turnstile_or_clearance(
        self,
        token: str = '',
        clearance: str = '',
        ip_address: str = None,
    ) -> Tuple[bool, str]:
        """Accept either a fresh Turnstile token or a local short-lived clearance."""
        if not self.turnstile_enabled:
            return True, 'turnstile_disabled'
        clearance_ok, clearance_msg = self.verify_turnstile_clearance(clearance, ip_address)
        if clearance_ok:
            return True, clearance_msg
        return self.verify_turnstile(token, ip_address)
    
    # =========================================================================
    # Rate Limiting & Brute-Force Protection
    # =========================================================================
    
    def record_login_attempt(self, identifier: str, identifier_type: str, 
                             success: bool, ip_address: str = None, 
                             user_agent: str = None) -> bool:
        """
        Record a login attempt for rate limiting.
        
        Args:
            identifier: IP address or username
            identifier_type: 'ip' or 'account'
            success: Whether the attempt was successful
            ip_address: Client IP address
            user_agent: Client user agent string
        """
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    INSERT INTO qd_login_attempts 
                    (identifier, identifier_type, success, ip_address, user_agent)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (identifier, identifier_type, success, ip_address, user_agent)
                )
                db.commit()
                cur.close()
            return True
        except Exception as e:
            logger.error(f"Failed to record login attempt: {e}")
            return False
    
    def is_blocked(self, identifier: str, identifier_type: str) -> Tuple[bool, int]:
        """
        Check if an identifier (IP or account) is blocked due to too many failed attempts.
        
        Returns:
            (is_blocked, remaining_seconds)
        """
        try:
            if identifier_type == 'ip':
                max_attempts = self.ip_max_attempts
                window_minutes = self.ip_window_minutes
                block_minutes = self.ip_block_minutes
            else:  # account
                max_attempts = self.account_max_attempts
                window_minutes = self.account_window_minutes
                block_minutes = self.account_block_minutes
            
            with get_db_connection() as db:
                cur = db.cursor()
                
                # Use the database clock for timestamps written by the database.
                cur.execute(
                    """
                    WITH recent_failures AS (
                        SELECT attempt_time
                        FROM qd_login_attempts
                        WHERE identifier = ?
                          AND identifier_type = ?
                          AND success = FALSE
                          AND attempt_time > NOW() - make_interval(mins => ?)
                    ),
                    threshold_events AS (
                        SELECT current_attempt.attempt_time AS block_started_at
                        FROM recent_failures current_attempt
                        WHERE (
                            SELECT COUNT(*)
                            FROM recent_failures prior_attempt
                            WHERE prior_attempt.attempt_time BETWEEN
                                current_attempt.attempt_time - make_interval(mins => ?)
                                AND current_attempt.attempt_time
                        ) >= ?
                    )
                    SELECT
                        GREATEST(
                            0,
                            COALESCE(
                                EXTRACT(EPOCH FROM (
                                    MAX(block_started_at)
                                    + make_interval(mins => ?)
                                    - NOW()
                                )),
                                0
                            )
                        )::INTEGER AS remaining_seconds
                    FROM threshold_events
                    """,
                    (
                        identifier,
                        identifier_type,
                        window_minutes + block_minutes,
                        window_minutes,
                        max_attempts,
                        block_minutes,
                    )
                )
                row = cur.fetchone()
                db.rollback()
                cur.close()
                
                if not row:
                    return False, 0
                
                remaining = int(row.get('remaining_seconds') or 0)
                
                if remaining > 0:
                    return True, remaining
                
                return False, 0
                
        except Exception as e:
            logger.error(f"Failed to check block status: {e}")
            return False, 0
    
    def check_login_allowed(self, username: str, ip_address: str) -> Tuple[bool, str]:
        """
        Check if login is allowed for the given username and IP.
        
        Returns:
            (allowed, message)
        """
        # Check IP block
        ip_blocked, ip_remaining = self.is_blocked(ip_address, 'ip')
        if ip_blocked:
            minutes = ip_remaining // 60
            return False, f'Too many failed attempts from this IP. Try again in {minutes + 1} minutes.'
        
        # Check account block
        account_blocked, account_remaining = self.is_blocked(username, 'account')
        if account_blocked:
            minutes = account_remaining // 60
            return False, f'Account temporarily locked due to too many failed attempts. Try again in {minutes + 1} minutes.'
        
        return True, 'allowed'
    
    def clear_login_attempts(self, identifier: str, identifier_type: str) -> bool:
        """
        Clear login attempts for an identifier (called after successful login).
        """
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    DELETE FROM qd_login_attempts
                    WHERE identifier = ? AND identifier_type = ?
                    """,
                    (identifier, identifier_type)
                )
                db.commit()
                cur.close()
            return True
        except Exception as e:
            logger.error(f"Failed to clear login attempts: {e}")
            return False
    
    # =========================================================================
    # Security Audit Logging
    # =========================================================================
    
    def log_security_event(self, action: str, user_id: int = None, 
                          ip_address: str = None, user_agent: str = None,
                          details: dict = None) -> bool:
        """
        Log a security-related event.
        
        Args:
            action: Event type (login, logout, register, reset_password, etc.)
            user_id: User ID if applicable
            ip_address: Client IP
            user_agent: Client user agent
            details: Additional details as dict
        """
        try:
            details_json = json.dumps(details) if details else None
            
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    INSERT INTO qd_security_logs 
                    (user_id, action, ip_address, user_agent, details)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (user_id, action, ip_address, user_agent, details_json)
                )
                db.commit()
                cur.close()
            return True
        except Exception as e:
            logger.error(f"Failed to log security event: {e}")
            return False
    
    # =========================================================================
    # Verification Code Rate Limiting
    # =========================================================================
    
    def can_send_verification_code(self, email: str, ip_address: str) -> Tuple[bool, str]:
        """
        Check if we can send a verification code to this email from this IP.
        
        Returns:
            (allowed, message)
        """
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                
                # Check email rate limit (one code per minute per email).
                # The database clock also writes created_at, so keep both sides
                # of the comparison in the database timezone.
                cur.execute(
                    """
                    SELECT COUNT(*) as count FROM qd_verification_codes
                    WHERE email = ?
                      AND created_at > NOW() - make_interval(secs => ?)
                    """,
                    (email, self.code_rate_limit_seconds)
                )
                row = cur.fetchone()
                if row and row['count'] > 0:
                    db.rollback()
                    cur.close()
                    return False, f'Please wait {self.code_rate_limit_seconds} seconds before requesting another code'
                
                # Check IP hourly limit
                cur.execute(
                    """
                    SELECT COUNT(*) as count FROM qd_verification_codes
                    WHERE ip_address = ?
                      AND created_at > NOW() - INTERVAL '1 hour'
                    """,
                    (ip_address,)
                )
                row = cur.fetchone()
                if row and row['count'] >= self.code_ip_hourly_limit:
                    db.rollback()
                    cur.close()
                    return False, 'Too many verification code requests from this IP. Try again later.'
                
                db.rollback()
                cur.close()
                return True, 'allowed'
                
        except Exception as e:
            logger.error(f"Failed to check verification code rate limit: {e}")
            return True, 'allowed'  # Fail open on DB errors

    def reserve_registration_slot(
        self,
        ip_address: str,
        user_agent: str = None,
        email: str = None,
    ) -> Tuple[bool, str]:
        """Atomically reserve one account-creation slot for an IP address.

        Verification-code throttling alone is insufficient because a client can
        request codes through several egress addresses and submit every final
        registration through one address.  The same hourly setting therefore
        also caps completed account-creation attempts per registration IP.
        """
        ip = str(ip_address or '').strip()
        if not ip:
            return False, 'Unable to verify the registration source. Try again later.'

        try:
            with get_db_connection() as db:
                cur = db.cursor()
                lock_key = f'registration-rate:{ip}'
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(?))",
                    (lock_key,),
                )

                cur.execute(
                    """
                    SELECT COUNT(*) as count FROM qd_security_logs
                    WHERE action = 'registration_rate_slot'
                      AND ip_address = ?
                      AND created_at > NOW() - INTERVAL '1 hour'
                    """,
                    (ip,),
                )
                row = cur.fetchone()
                registration_count = int((row or {}).get('count') or 0)
                if registration_count >= self.code_ip_hourly_limit:
                    db.rollback()
                    cur.close()
                    return False, 'Too many accounts created from this IP. Try again later.'

                details = {
                    'email_hash': hashlib.sha256(
                        str(email or '').strip().lower().encode('utf-8')
                    ).hexdigest()
                }
                cur.execute(
                    """
                    INSERT INTO qd_security_logs
                    (user_id, action, ip_address, user_agent, details)
                    VALUES (?, 'registration_rate_slot', ?, ?, ?)
                    """,
                    (None, ip, user_agent, json.dumps(details)),
                )
                db.commit()
                cur.close()
                return True, 'allowed'
        except Exception as e:
            logger.error(f"Failed to reserve registration rate-limit slot: {e}")
            return False, 'Registration protection is temporarily unavailable. Try again later.'
    
    # =========================================================================
    # Password Strength Validation
    # =========================================================================
    
    def validate_password_strength(self, password: str) -> Tuple[bool, str]:
        """
        Validate password meets minimum security requirements.
        
        Requirements:
        - At least 8 characters
        - Contains at least one uppercase letter
        - Contains at least one lowercase letter
        - Contains at least one digit
        
        Returns:
            (valid, message)
        """
        if len(password) < 8:
            return False, 'Password must be at least 8 characters long'
        
        if not any(c.isupper() for c in password):
            return False, 'Password must contain at least one uppercase letter'
        
        if not any(c.islower() for c in password):
            return False, 'Password must contain at least one lowercase letter'
        
        if not any(c.isdigit() for c in password):
            return False, 'Password must contain at least one digit'
        
        return True, 'valid'
    
    # =========================================================================
    # Cleanup
    # =========================================================================
    
    def cleanup_old_records(self, days: int = 7) -> int:
        """
        Clean up old login attempts and expired verification codes.
        
        Returns:
            Number of records deleted
        """
        deleted = 0
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                
                # Clean old login attempts
                cur.execute(
                    """
                    DELETE FROM qd_login_attempts
                    WHERE attempt_time < NOW() - make_interval(days => ?)
                    """,
                    (days,)
                )
                deleted += cur.rowcount or 0
                
                # Clean expired verification codes
                cur.execute(
                    """
                    DELETE FROM qd_verification_codes
                    WHERE expires_at < NOW() - make_interval(days => ?)
                    """,
                    (days,)
                )
                deleted += cur.rowcount or 0
                
                db.commit()
                cur.close()
                
            logger.info(f"Security cleanup: deleted {deleted} old records")
            return deleted
            
        except Exception as e:
            logger.error(f"Security cleanup failed: {e}")
            return 0
