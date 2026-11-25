export interface UserRef {
  id: string;
  displayName: string;
  avatarUrl: string | null;
}

export interface User extends UserRef {
  id: string;
  email: string;
  createdAt: string;
  updatedAt: string;
}
