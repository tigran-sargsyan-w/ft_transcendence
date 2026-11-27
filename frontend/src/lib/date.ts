import { format } from "date-fns";

export function formateDate(date: string) {
  return format(date, "dd/MM/yyyy HH:mm");
}
