import { useState } from "react";
import { loginUser, registerUser } from "./auth";
import { ApiError } from "@/services/api";
import { Input } from "@/components/ui/input";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Button } from "@/components/ui/button";
import type { SubmitEvent } from "react";
import { useNavigate } from "react-router";
import { useAuth } from "./AuthContext";

type FormStatus = "idle" | "submitting" | "success";
type RegisterCardProps = { onSwitch: () => void };

function isValidEmail(email: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

export function RegisterCard({ onSwitch }: RegisterCardProps) {
  let navigate = useNavigate();

  const { setUser } = useAuth();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [status, setStatus] = useState<FormStatus>("idle");
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    const trimmedEmail = email.trim();

    if (!isValidEmail(trimmedEmail)) {
      setError("Enter a valid email address.");
      return;
    }

    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }

    if (password.length > 128) {
      setError("Password must be at most 128 characters.");
      return;
    }

    const displayName = [firstName, lastName]
      .map((part) => part.trim())
      .filter(Boolean)
      .join(" ");

    if (!firstName.trim() || !lastName.trim()) {
      setError("Enter your first and last name.");
      return;
    }

    if (displayName.length > 64) {
      setError("Your name must be at most 64 characters.");
      return;
    }

    setStatus("submitting");

    try {
      await registerUser({
        email: trimmedEmail,
        password,
        displayName,
      });

      setStatus("success");
      const user = await loginUser({
        email: trimmedEmail,
        password,
      });
      setPassword("");
      setUser(user);
      navigate("/dashboard");
    } catch (err) {
      setStatus("idle");

      if (err instanceof ApiError) {
        setError(err.message);
        return;
      }

      setError("Registration failed. Please try again.");
    }
  }

  const isSubmitting = status === "submitting";

  return (
    <form onSubmit={handleSubmit} noValidate className="w-full max-w-100">
      <Card>
        <CardHeader>
          <CardTitle>Sign up</CardTitle>
          <CardDescription>Create your account to get started.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col">
          <FieldGroup>
            <Field>
              <FieldLabel>Email</FieldLabel>
              <Input
                id="email"
                name="email"
                type="email"
                autoComplete="email"
                required
                value={email}
                disabled={isSubmitting}
                onChange={(event) => setEmail(event.target.value)}
              />
            </Field>
            <Field>
              <FieldLabel>Password</FieldLabel>
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete="new-password"
                required
                minLength={8}
                maxLength={128}
                value={password}
                disabled={isSubmitting}
                onChange={(event) => setPassword(event.target.value)}
              />
            </Field>
            <Field>
              <FieldLabel>First Name</FieldLabel>
              <Input
                id="firstName"
                name="firstName"
                type="text"
                autoComplete="given-name"
                required
                value={firstName}
                disabled={isSubmitting}
                onChange={(event) => setFirstName(event.target.value)}
              />
            </Field>
            <Field>
              <FieldLabel>Last Name</FieldLabel>
              <Input
                id="lastName"
                name="lastName"
                type="text"
                autoComplete="family-name"
                required
                value={lastName}
                disabled={isSubmitting}
                onChange={(event) => setLastName(event.target.value)}
              />
            </Field>
          </FieldGroup>
        </CardContent>
        <CardFooter className="flex flex-col">
          <Button type="submit" disabled={isSubmitting} className="w-full">
            {isSubmitting ? "Signing up…" : "Sign up"}
          </Button>
          <CardAction>
            Already have an account?{" "}
            <Button type="button" onClick={onSwitch} variant="link">
              Sign in
            </Button>
          </CardAction>
          {error ? (
            <p className="text-sm text-red-700" role="alert">
              {error}
            </p>
          ) : null}
        </CardFooter>
      </Card>
    </form>
  );
}
