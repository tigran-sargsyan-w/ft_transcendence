import { useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router";
import { RegisterError, registerUser } from "../services/auth";
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

type FormStatus = "idle" | "submitting" | "success";
type RegisterCardProps = { onSwitch: () => void };

function isValidEmail(email: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

export function RegisterCard({ onSwitch }: RegisterCardProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState<FormStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [registeredEmail, setRegisteredEmail] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
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

    if (password.length > 64) {
      setError("Password must be at most 64 characters.");
      return;
    }

    setStatus("submitting");

    try {
      const user = await registerUser({
        email: trimmedEmail,
        password,
      });

      setRegisteredEmail(user.email);
      setStatus("success");
      setPassword("");
    } catch (err) {
      setStatus("idle");

      if (err instanceof RegisterError) {
        setError(err.message);
        return;
      }

      setError("Registration failed. Please try again.");
    }
  }

  if (status === "success" && registeredEmail) {
    return (
      <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-6 py-12">
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-900">
          Account created
        </h1>
        <p className="mt-3 text-sm text-zinc-600">
          Registered as{" "}
          <span className="font-medium text-zinc-900">{registeredEmail}</span>.
          Login will be available once the session API is ready.
        </p>
        <p className="mt-6 text-sm text-zinc-600">
          <Link
            className="underline underline-offset-2 hover:text-zinc-900"
            to="/"
          >
            Back to home
          </Link>
        </p>
      </main>
    );
  }

  const isSubmitting = status === "submitting";

  return (
    <form noValidate className="w-full max-w-100">
      <Card>
        <CardHeader>
          <CardTitle>Sign up</CardTitle>
          <CardDescription>Register with email and password.</CardDescription>
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
                maxLength={64}
                value={password}
                disabled={isSubmitting}
                onChange={(event) => setPassword(event.target.value)}
              />
            </Field>
          </FieldGroup>
        </CardContent>
        <CardFooter className="flex flex-col">
          <Button type="submit" disabled={isSubmitting} className="w-full">
            {isSubmitting ? "Signing up…" : "Signup up"}
          </Button>
          <CardAction>
            Already have an account?{" "}
            <Button onClick={onSwitch} variant="link">
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
