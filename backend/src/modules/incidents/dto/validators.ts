import { Transform } from 'class-transformer';
import { ValidateBy, ValidateIf } from 'class-validator';

// Leading and trailing spaces are not part of a title or a note.
export const Trim = () =>
  Transform(({ value }) => (typeof value === 'string' ? value.trim() : value));

const PLAIN_TEXT = /^(?:[^\0\ud800-\udfff]|[\ud800-\udbff][\udc00-\udfff])*$/;

// PostgreSQL refuses a NUL character in text, and a lone surrogate in JSON
// (the events keep the texts in a JSON column): either would be a 500. The
// pattern takes any character but those, and a surrogate only in a pair. A
// value that is not text passes here: `@IsString()` is the one to say so.
export const PlainText = () =>
  ValidateBy({
    name: 'plainText',
    validator: {
      validate: (value: unknown) =>
        typeof value !== 'string' || PLAIN_TEXT.test(value),
      defaultMessage: (args) =>
        `${args?.property} must not contain a NUL character or a lone surrogate`,
    },
  });

// The field may be absent, but `null` is not a value (`@IsOptional` lets it
// through).
export const OptionalField = () =>
  ValidateIf((_object, value) => value !== undefined);
