import { Transform } from 'class-transformer';
import { Matches, ValidateIf } from 'class-validator';

// Leading and trailing spaces are not part of a title or a note.
export const Trim = () =>
  Transform(({ value }) => (typeof value === 'string' ? value.trim() : value));

// PostgreSQL refuses a NUL character in text, and a lone surrogate in JSON
// (the events keep the texts in a JSON column): either would be a 500. The
// pattern takes any character but those, and a surrogate only in a pair.
export const PlainText = () =>
  Matches(/^(?:[^\0\ud800-\udfff]|[\ud800-\udbff][\udc00-\udfff])*$/, {
    message: '$property must not contain a NUL character or a lone surrogate',
  });

// The field may be absent, but `null` is not a value (`@IsOptional` lets it
// through).
export const OptionalField = () =>
  ValidateIf((_object, value) => value !== undefined);
