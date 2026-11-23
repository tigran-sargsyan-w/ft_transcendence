import {
  NavigationMenu,
  NavigationMenuItem,
  NavigationMenuLink,
  NavigationMenuList,
  navigationMenuTriggerStyle,
} from "@/components/ui/navigation-menu";
import { NavLink } from "react-router";
import { cn } from "@/lib/utils";
import { NAV_ITEMS } from "./navItems";

export function Navbar() {
  return (
    <NavigationMenu>
      <NavigationMenuList>
        {NAV_ITEMS.map(({ label, to }) => (
          <NavigationMenuItem key={to}>
            <NavigationMenuLink
              asChild
              className={cn(
                navigationMenuTriggerStyle(),
                "aria-[current=page]:bg-primary aria-[current=page]:text-primary-foreground",
                "aria-[current=page]:hover:bg-primary/90 aria-[current=page]:focus:bg-primary/90",
              )}
            >
              <NavLink to={to}>{label}</NavLink>
            </NavigationMenuLink>
          </NavigationMenuItem>
        ))}
      </NavigationMenuList>
    </NavigationMenu>
  );
}
