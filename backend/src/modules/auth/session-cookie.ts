import type { CookieOptions } from 'express';

export const SESSION_COOKIE = 'sid';

const ttlHours = Number(process.env.SESSION_TTL_HOURS ?? 168);

if (!Number.isFinite(ttlHours) || ttlHours <= 0) {
  throw new Error('SESSION_TTL_HOURS must be a positive number');
}

export const SESSION_TTL_MS = ttlHours * 60 * 60 * 1000;

// Secure unless turned off for local development over plain HTTP (ADR 0002).
export const sessionCookieOptions: CookieOptions = {
  httpOnly: true,
  secure: process.env.COOKIE_SECURE !== 'false',
  sameSite: 'lax',
  path: '/',
};
