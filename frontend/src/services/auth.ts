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

type ApiErrorBody = {
  error?: {
    code?: string
    message?: string
  }
}

type ApiSuccessBody = {
  data?: RegisteredUser
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

  const body = (await response.json().catch(() => null)) as
    | (ApiErrorBody & ApiSuccessBody)
    | null

  if (response.ok) {
    if (!body?.data) {
      throw new RegisterError(
        'UNKNOWN',
        'Registration succeeded but returned an unexpected response.',
        response.status,
      )
    }

    return body.data
  }

  const code = body?.error?.code
  const message = body?.error?.message

  if (response.status === 409 && code === 'EMAIL_ALREADY_EXISTS') {
    throw new RegisterError(
      'EMAIL_ALREADY_EXISTS',
      typeof message === 'string' ? message : 'Email is already registered',
      response.status,
    )
  }

  if (response.status === 400) {
    throw new RegisterError(
      'VALIDATION_ERROR',
      typeof message === 'string' ? message : 'Invalid registration input',
      response.status,
    )
  }

  throw new RegisterError(
    'UNKNOWN',
    typeof message === 'string'
      ? message
      : `Registration failed with status ${response.status}`,
    response.status,
  )
}
