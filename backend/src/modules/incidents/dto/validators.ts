import { Transform } from 'class-transformer';
import { Matches, ValidateIf } from 'class-validator';

// Leading and trailing spaces are not part of a title or a note.
export const Trim = () =>
  Transform(({ value }) => (typeof value === 'string' ? value.trim() : value));

// PostgreSQL refuses a NUL character in text: it would be a 500.
export const NoNul = () =>
  Matches(/^[^\0]*$/, {
    message: '$property must not contain a NUL character',
  });

// The field may be absent, but `null` is not a value (`@IsOptional` lets it
// through).
export const OptionalField = () =>
  ValidateIf((_object, value) => value !== undefined);
