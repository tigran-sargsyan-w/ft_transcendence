export interface RegisterInput {
  email: string
  password: string
}

export interface RegisteredUser {
  id: string
  email: string
  createdAt: string
  updatedAt: string
}

export type RegisterErrorCode =
  | 'EMAIL_ALREADY_EXISTS'
  | 'VALIDATION_ERROR'
  | 'NETWORK_ERROR'
  | 'UNKNOWN'

export class RegisterError extends Error {
  readonly code: RegisterErrorCode
  readonly status: number

  constructor(code: RegisterErrorCode, message: string, status: number) {
    super(message)
    this.name = 'RegisterError'
    this.code = code
    this.status = status
  }
}

export async function registerUser(
  input: RegisterInput,
): Promise<RegisteredUser> {
  let response: Response

  try {
    response = await fetch('/api/v1/auth/register', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(input),
    })
  } catch {
    throw new RegisterError(
      'NETWORK_ERROR',
      'Cannot reach the backend. Is it running on port 3000?',
      0,
    )
  }

  if (response.ok) {
    return response.json() as Promise<RegisteredUser>
  }

  const body = (await response.json().catch(() => null)) as {
    code?: string
    message?: string | string[]
  } | null

  if (response.status === 409 && body?.code === 'EMAIL_ALREADY_EXISTS') {
    throw new RegisterError(
      'EMAIL_ALREADY_EXISTS',
      typeof body.message === 'string'
        ? body.message
        : 'Email is already registered',
      response.status,
    )
  }

  if (response.status === 400) {
    const message = Array.isArray(body?.message)
      ? body.message.join(', ')
      : typeof body?.message === 'string'
        ? body.message
        : 'Invalid registration input'

    throw new RegisterError('VALIDATION_ERROR', message, response.status)
  }

  throw new RegisterError(
    'UNKNOWN',
    `Registration failed with status ${response.status}`,
    response.status,
  )
}
