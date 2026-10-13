import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router'
import { RegisterError, registerUser } from '../services/auth'

type FormStatus = 'idle' | 'submitting' | 'success'

function isValidEmail(email: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)
}

export function RegisterPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [status, setStatus] = useState<FormStatus>('idle')
  const [error, setError] = useState<string | null>(null)
  const [registeredEmail, setRegisteredEmail] = useState<string | null>(null)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)

    const trimmedEmail = email.trim()

    if (!isValidEmail(trimmedEmail)) {
      setError('Enter a valid email address.')
      return
    }

    if (password.length < 8) {
      setError('Password must be at least 8 characters.')
      return
    }

    if (password.length > 64) {
      setError('Password must be at most 64 characters.')
      return
    }

    setStatus('submitting')

    try {
      const user = await registerUser({
        email: trimmedEmail,
        password,
      })

      setRegisteredEmail(user.email)
      setStatus('success')
      setPassword('')
    } catch (err) {
      setStatus('idle')

      if (err instanceof RegisterError) {
        setError(err.message)
        return
      }

      setError('Registration failed. Please try again.')
    }
  }

  if (status === 'success' && registeredEmail) {
    return (
      <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-6 py-12">
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-900">
          Account created
        </h1>
        <p className="mt-3 text-sm text-zinc-600">
          Registered as <span className="font-medium text-zinc-900">{registeredEmail}</span>.
          Login will be available once the session API is ready.
        </p>
        <p className="mt-6 text-sm text-zinc-600">
          <Link className="underline underline-offset-2 hover:text-zinc-900" to="/">
            Back to home
          </Link>
        </p>
      </main>
    )
  }

  const isSubmitting = status === 'submitting'

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-6 py-12">
      <h1 className="text-2xl font-semibold tracking-tight text-zinc-900">
        Create account
      </h1>
      <p className="mt-2 text-sm text-zinc-600">
        Register with email and password.
      </p>

      <form className="mt-8 space-y-5" onSubmit={handleSubmit} noValidate>
        <div className="space-y-2">
          <label className="block text-sm font-medium text-zinc-800" htmlFor="email">
            Email
          </label>
          <input
            id="email"
            name="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            disabled={isSubmitting}
            onChange={(event) => setEmail(event.target.value)}
            className="w-full border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 outline-none focus:border-zinc-500 disabled:opacity-60"
          />
        </div>

        <div className="space-y-2">
          <label className="block text-sm font-medium text-zinc-800" htmlFor="password">
            Password
          </label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="new-password"
            required
            minLength={8}
            maxLength={64}
            value={password}
            disabled={isSubmitting}
            onChange={(event) => setPassword(event.target.value)}
            className="w-full border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 outline-none focus:border-zinc-500 disabled:opacity-60"
          />
          <p className="text-xs text-zinc-500">At least 8 characters.</p>
        </div>

        {error ? (
          <p className="text-sm text-red-700" role="alert">
            {error}
          </p>
        ) : null}

        <button
          type="submit"
          disabled={isSubmitting}
          className="w-full border border-zinc-900 bg-zinc-900 px-3 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isSubmitting ? 'Creating account…' : 'Create account'}
        </button>
      </form>

      <p className="mt-6 text-sm text-zinc-600">
        Already have an account?{' '}
        <span className="text-zinc-400">Login coming soon</span>
      </p>
    </main>
  )
}
