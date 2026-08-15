import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE, verifySessionToken } from "./lib/auth";

/**
 * Giriş kapısı. /giris ve giriş API'si dışındaki her şey oturum ister.
 * Reddit ve Supabase anahtarlarına giden tek yol buradan geçiyor.
 */
export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  const isPublic =
    pathname === "/giris" ||
    pathname === "/api/giris" ||
    pathname === "/manifest.webmanifest";

  if (isPublic) return NextResponse.next();

  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (await verifySessionToken(token)) return NextResponse.next();

  if (pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "Oturum gerekli" }, { status: 401 });
  }

  return NextResponse.redirect(new URL("/giris", request.url));
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.png).*)"],
};
