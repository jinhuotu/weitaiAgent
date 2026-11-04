import { apiRequest } from './api';
import { getAccessToken } from './auth';

export type RoleItem = {
  id: number;
  code: string;
  name: string;
  description: string | null;
  menus: string[];
  userCount: number;
  createdAt: number;
  updatedAt: number;
};

export type UserItem = {
  id: number;
  username: string;
  displayName: string | null;
  email: string | null;
  department: string | null;
  phone: string | null;
  isActive: boolean;
  isSuperuser: boolean;
  roles: string[];
  roleNames: string[];
  remark?: string | null;
  lastLoginAt: number | null;
  createdAt: number;
  updatedAt: number;
};

export type CreateUserBody = {
  username: string;
  password: string;
  displayName?: string;
  email?: string;
  department?: string;
  phone?: string;
  roleCodes?: string[];
  isActive?: boolean;
  isSuperuser?: boolean;
  remark?: string;
};

export type UpdateUserBody = {
  displayName?: string | null;
  email?: string | null;
  department?: string | null;
  phone?: string | null;
  roleCodes?: string[];
  isActive?: boolean;
  isSuperuser?: boolean;
  remark?: string | null;
};

function requireToken(): string {
  const token = getAccessToken();
  if (!token) throw new Error('请先登录');
  return token;
}

export type CreateRoleBody = {
  name: string;
  description?: string;
  menus?: string[];
};

export type UpdateRoleBody = {
  name?: string;
  description?: string | null;
  menus?: string[];
};

export async function listRoles(): Promise<RoleItem[]> {
  const data = await apiRequest<{ items: RoleItem[] }>('/api/v1/roles', {
    token: requireToken(),
  });
  return data.items || [];
}

export async function createRole(body: CreateRoleBody): Promise<RoleItem> {
  const data = await apiRequest<{ item: RoleItem }>('/api/v1/roles', {
    method: 'POST',
    token: requireToken(),
    body,
  });
  return data.item;
}

export async function updateRole(id: number, body: UpdateRoleBody): Promise<RoleItem> {
  const data = await apiRequest<{ item: RoleItem }>(`/api/v1/roles/${id}`, {
    method: 'PATCH',
    token: requireToken(),
    body,
  });
  return data.item;
}

export async function deleteRole(id: number): Promise<void> {
  await apiRequest(`/api/v1/roles/${id}`, {
    method: 'DELETE',
    token: requireToken(),
  });
}

export async function listUsers(): Promise<UserItem[]> {
  const data = await apiRequest<{ items: UserItem[] }>('/api/v1/users', {
    token: requireToken(),
  });
  return data.items || [];
}

export async function createUser(body: CreateUserBody): Promise<UserItem> {
  const data = await apiRequest<{ item: UserItem }>('/api/v1/users', {
    method: 'POST',
    token: requireToken(),
    body,
  });
  return data.item;
}

export async function updateUser(id: number, body: UpdateUserBody): Promise<UserItem> {
  const data = await apiRequest<{ item: UserItem }>(`/api/v1/users/${id}`, {
    method: 'PATCH',
    token: requireToken(),
    body,
  });
  return data.item;
}

export async function resetUserPassword(id: number, password: string): Promise<void> {
  await apiRequest(`/api/v1/users/${id}/reset-password`, {
    method: 'POST',
    token: requireToken(),
    body: { password },
  });
}

export async function deleteUser(id: number): Promise<void> {
  await apiRequest(`/api/v1/users/${id}`, {
    method: 'DELETE',
    token: requireToken(),
  });
}
