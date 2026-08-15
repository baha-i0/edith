import { getSettings } from "@/lib/db";
import { SettingsForm } from "@/components/SettingsForm";

export const dynamic = "force-dynamic";

export default async function AyarlarPage() {
  const settings = await getSettings();

  return (
    <div className="max-w-md space-y-4">
      <div>
        <h1 className="text-lg font-semibold">Ayarlar</h1>
        <p className="mt-1 text-sm text-inkMuted">
          Atak seviyesi üslubu ve eşiği değiştirir — kurallar motoru ve hız
          limitleri her seviyede aynı şekilde çalışmaya devam eder.
        </p>
      </div>

      <SettingsForm initial={settings} />
    </div>
  );
}
