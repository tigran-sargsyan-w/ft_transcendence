import { useState } from "react";
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

type LoginCardProps = { onSwitch: () => void };

// type FormStatus = "idle" | "submitting" | "success";

// function isValidEmail(email: string): boolean {
//   return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
// }

export function LoginCard({ onSwitch }: LoginCardProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // const [status, setStatus] = useState<FormStatus>("idle");
  const [error, setError] = useState<string | null>(null);

  // async function handleSubmit(event: FormEvent<HTMLFormElement>) {
  //   event.preventDefault();
  //   setError(null);

  //   const trimmedEmail = email.trim();

  //   if (!isValidEmail(trimmedEmail)) {
  //     setError("Enter a valid email address.");
  //     return;
  //   }

  //   if (password.length < 8) {
  //     setError("Password must be at least 8 characters.");
  //     return;
  //   }

  //   if (password.length > 64) {
  //     setError("Password must be at most 64 characters.");
  //     return;
  //   }

  //   setStatus("submitting");

  //   try {
  //     const user = await registerUser({
  //       email: trimmedEmail,
  //       password,
  //     });

  //     setRegisteredEmail(user.email);
  //     setStatus("success");
  //     setPassword("");
  //   } catch (err) {
  //     setStatus("idle");

  //     if (err instanceof RegisterError) {
  //       setError(err.message);
  //       return;
  //     }

  //     setError("Registration failed. Please try again.");
  //   }
  // }

  const isSubmitting = status === "submitting";

  return (
    <form noValidate className="w-full max-w-100">
      <Card>
        <CardHeader>
          <CardTitle>Sign in</CardTitle>
          <CardDescription>Start using our platform.</CardDescription>
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
                value={password}
                disabled={isSubmitting}
                onChange={(event) => setPassword(event.target.value)}
              />
            </Field>
          </FieldGroup>
        </CardContent>
        <CardFooter className="flex flex-col">
          <Button type="submit" disabled={isSubmitting} className="w-full">
            {isSubmitting ? "Signing in…" : "Sign in"}
          </Button>
          <CardAction>
            Don't have an account?
            <Button onClick={onSwitch} variant="link">
              Sign up
            </Button>
          </CardAction>
          {error ? (
            <p className="text-xs text-destructive" role="alert">
              {error}
            </p>
          ) : null}
        </CardFooter>
      </Card>
    </form>
  );
}
