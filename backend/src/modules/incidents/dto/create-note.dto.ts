import { IsNotEmpty, IsString, MaxLength } from 'class-validator';
import { PlainText, Trim } from './validators.js';

export class CreateNoteDto {
  // Plain text, as the chat messages will be.
  @Trim()
  @IsString()
  @IsNotEmpty()
  @MaxLength(2000)
  @PlainText()
  body!: string;
}
