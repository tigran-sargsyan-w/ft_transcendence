export interface User {
  id: string;
  email: string;
  createdAt: string;
  updatedAt: string;
}

export interface UserRef {
  id: string;
  displayName: string;
  avatarUrl: string | null;
}
