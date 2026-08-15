import { NextResponse } from "next/server";
import {
  SESSION_COOKIE,
  SESSION_MAX_AGE,
  checkPassword,
  createSessionToken,
} from "@/lib/auth";

export async function POST(request: Request) {
  const { password } = (await request.json()) as { password?: string };

  if (!password || !checkPassword(password)) {
    // Kaba kuvvet denemesini yavaşlatan küçük gecikme.
    await new Promise((r) => setTimeout(r, 600));
    return NextResponse.json({ error: "Şifre yanlış" }, { status: 401 });
  }

  const response = NextResponse.json({ ok: true });
  response.cookies.set(SESSION_COOKIE, await createSessionToken(), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_MAX_AGE,
  });
  return response;
}
