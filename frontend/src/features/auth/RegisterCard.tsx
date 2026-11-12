import { useState } from "react";
import { Link } from "react-router";
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

type FormStatus = "idle" | "submitting" | "success";
type RegisterCardProps = { onSwitch: () => void };

function isValidEmail(email: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

export function RegisterCard({ onSwitch }: RegisterCardProps) {
  let navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
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

    if (password.length > 64) {
      setError("Password must be at most 64 characters.");
      return;
    }

    setStatus("submitting");

    try {
      await registerUser({
        email: trimmedEmail,
        password,
      });

      setStatus("success");
      await loginUser({
        email: trimmedEmail,
        password,
      });
      setPassword("");
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
