import { ContactLink, LegalPage } from "@/components/LegalPage";

export default function Terms() {
  return (
    <LegalPage title="Terms of Service" updated="27 November 2026">
      <p>
        By creating an account or signing in to ft_transcendence, you agree to
        these terms.
      </p>

      <h2>1. What this service is</h2>
      <p>
        ft_transcendence is an educational project made by students at 42. It is
        provided as it is, to learn and to demonstrate what we built. It is not
        a commercial service.
      </p>

      <h2>2. Your account</h2>
      <ul>
        <li>
          You must give a valid email address and choose a password only you
          know.
        </li>
        <li>
          You are responsible for what happens under your account. Sign out on
          shared computers.
        </li>
        <li>
          Please choose a display name that is not offensive and does not
          impersonate someone else.
        </li>
      </ul>

      <h2>3. Acceptable use</h2>
      <ul>
        <li>
          Do not install or use the application to monitor a Docker environment
          you are not allowed to inspect.
        </li>
        <li>
          Do not try to break, overload or bypass the application, other
          accounts, or its protections.
        </li>
        <li>
          Do not post unlawful, abusive or private content (for example in
          incident titles and notes, which every signed-in user can read).
        </li>
        <li>Do not upload an avatar you have no right to use.</li>
      </ul>

      <h2>4. Your content</h2>
      <p>
        You keep the rights to what you write. By posting it, you allow the
        application to store it and to show it to the other users as described
        in the Privacy Policy. Incident notes cannot be edited or erased by
        users once they are written.
      </p>

      <h2>5. No warranty, availability</h2>
      <p>
        The service may be slow, change or stop at any time, and data may be
        lost. We give no warranty of any kind, and we are not liable for damage
        caused by using the service, to the extent the law allows.
      </p>

      <h2>6. Ending your account</h2>
      <p>
        You can stop using the service at any time. To ask for the deletion of
        your account, <ContactLink /> (issues are public: give only your display
        name); the incident history is kept, as the Privacy Policy explains. We
        may suspend an account that breaks these terms.
      </p>

      <h2>7. Changes</h2>
      <p>
        We may change these terms. We update the date at the top when we do.
        Using the service after a change means you accept it.
      </p>

      <h2>8. Contact</h2>
      <p>
        To contact us, <ContactLink />.
      </p>
    </LegalPage>
  );
}
