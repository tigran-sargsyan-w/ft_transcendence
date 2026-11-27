import { ContactLink, LegalPage } from "@/components/LegalPage";

export default function Privacy() {
  return (
    <LegalPage title="Privacy Policy" updated="27 November 2026">
      <p>
        ft_transcendence is an educational project made by students at 42. It
        helps a team follow the state of a Docker environment and handle
        incidents together. This page explains what personal data the
        application keeps, who can see it and for how long. It is not legal
        advice.
      </p>
      <p>
        To contact us, <ContactLink />. Issues are public: never write your
        password or other private information in one.
      </p>

      <h2>1. What we store about you</h2>
      <ul>
        <li>
          <strong>Account:</strong> your email address, a hash of your password
          (argon2id; we never store the password itself), your display name
          (the sign-up form asks for your first and last name and uses them as
          your display name), an optional avatar, and the dates your account was
          created and updated.
        </li>
        <li>
          <strong>Session:</strong> when you sign in, we store a hash of your
          session token with its creation and expiry dates in the database; the
          token itself is not stored there.
        </li>
        <li>
          <strong>Avatar:</strong> if you upload one, the image is kept in our
          database. It is re-encoded to WebP, which removes the metadata of the
          original file, and the application refuses files that are too large.
        </li>
        <li>
          <strong>Friends:</strong> friend requests you send or receive, the
          friendships you accept, and their dates.
        </li>
        <li>
          <strong>Incidents:</strong> the titles, notes, severities, status
          changes and commander you set or are given, and the services
          concerned. Each entry keeps your user id and the time. Each change of
          a title, severity, status or commander is recorded in the incident
          history, and the previous value is kept in the database.
        </li>
      </ul>

      <h2>2. What other people can see</h2>
      <ul>
        <li>
          Every signed-in user can see your <strong>display name</strong> and
          your <strong>avatar</strong>, and can find you by searching for your
          display name. Choose a name you are comfortable sharing.
        </li>
        <li>
          Your <strong>email address</strong> is never shown to other users.
        </li>
        <li>
          Your <strong>friends</strong> (accepted requests only) can see
          whether you are online. This status is kept in the memory of the
          server only and is never written to the database. You appear online
          while you have at least one open connection, and offline a few
          seconds after the last one closes.
        </li>
        <li>
          <strong>
            Incidents and their notes are visible to every signed-in user
          </strong>
          , and new entries are sent live to everyone who has opened the
          incident room. Any signed-in user can name you as the commander of an
          incident; your name and avatar then appear in the incident lists.
          Anyone can create an account, and we do not check that an email
          address belongs to the person who gives it, so anyone who can reach
          the application can read all incidents. Do not write anything private
          in incidents.
        </li>
        <li>
          <strong>Monitored environment:</strong> the application shows the
          state of the Docker environment it runs on (see section 5) to every
          signed-in user.
        </li>
      </ul>

      <h2>3. Cookies and tracking</h2>
      <p>
        The application sets one cookie, <code>sid</code>, which keeps you
        signed in. It is not readable by scripts (HttpOnly), it is not sent
        with cross-site embedded content or cross-site form posts
        (SameSite=Lax), it is sent over HTTPS only (Secure) and it lasts 7
        days. The monitoring dashboards (Grafana, under /grafana/) are meant
        for the team that runs the application: they have no public sign-up,
        and Grafana sets its own session cookie when someone signs in to it. We
        do not use analytics, advertising or tracking, and we do not load any
        third-party resource: the fonts are served by the application itself.
      </p>

      <h2>4. IP addresses and logs</h2>
      <p>
        To limit abuse (for example repeated login attempts), the application
        counts requests per IP address in its memory only. These counters are
        not written to the database and disappear when the application
        restarts.
      </p>
      <p>
        The web server in front of the application keeps technical access logs:
        the time, the client IP address, the response status, the size of the
        response and how long it took. It is set up so that the address you
        request, your cookies and the content of your requests are not written
        to them. For some requests, for example those that its firewall blocks
        or finds suspicious, it keeps a minimal record (the time, the client IP
        address and technical identifiers), without request headers or
        contents. Serious server errors are logged as well and may contain more
        detail. The application itself also writes its error messages to its
        own container logs, and the database server keeps its own container
        logs, which can contain data from failed requests (for example an email
        address that is already registered). These
        logs are not written to the database and are not sent to any outside
        service. By default they stay on the server, in the container logs,
        until the containers are removed or recreated.
      </p>

      <h2>5. Data about monitored environments</h2>
      <p>
        The application monitors one Docker environment: the Docker engine of
        the machine it runs on. It lists the containers there, including
        containers unrelated to this project, and shows every signed-in user the
        service names, the Compose project, the images, the published ports, the
        networks, the volume names and the health status. It does not forward
        or store environment variables or command lines, and the host path of a
        bind mount is hidden.
      </p>

      <h2>6. How long we keep data</h2>
      <ul>
        <li>
          Sessions end after 7 days, or when you sign out. An expired session is
          deleted the next time it is used; one that is never used again stays
          stored (token hash and dates only) until your account is removed.
        </li>
        <li>Your avatar is kept until you replace or delete it.</li>
        <li>
          A friendship is removed when either of you removes it, and a pending
          request can be cancelled or declined.
        </li>
        <li>
          The technical logs described in section 4 are kept until the
          containers are removed or recreated.
        </li>
        <li>
          Incidents, notes and your account are kept until the project is
          stopped, or until the project maintainers delete them.
        </li>
      </ul>

      <h2>7. Your choices</h2>
      <p>
        You can sign out at any time from the user menu. Some features described
        on this page (avatars, friends, online status, incidents) exist in the
        API of the application before the interface offers them. Through that
        API you can change your display name, delete your avatar and remove
        friends. There is no button to delete your account. To ask what we keep about
        you, or to ask for deletion, <ContactLink /> and give only your display
        name. We delete your account on request, except that an account which
        created, commanded or wrote in an incident cannot be removed while that
        incident exists: the incident history is kept.
      </p>

      <h2>8. Security</h2>
      <p>
        Passwords are hashed with argon2id, the application is served over HTTPS
        and a web application firewall filters requests. No system is perfectly
        secure, so please use a password that you do not use elsewhere.
      </p>

      <h2>9. Changes</h2>
      <p>If we change this page, we update the date at the top.</p>
    </LegalPage>
  );
}
