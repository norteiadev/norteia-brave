<?php
// E2E of the image lane, norteia-api side. Read from stdin by `php` inside the api container
// (scripts/e2e_images.sh pipes it over ssh), so nothing is installed on the server:
//   php -- verify  <keys>             Brave photos per entity: collection, conversions, public resource
//   php -- cleanup <keys> [--apply]   delete the entities' Brave photos (origin=brave) + files
// <keys> = comma-separated source_refs (tripadvisor:attraction:N | ibge:UF:IBGE7).
// Only media with origin=brave is touched: the entities and any manual (Filament) photo stay.

require 'vendor/autoload.php';
$app = require 'bootstrap/app.php';
$app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();

use App\Models\Attraction;
use App\Models\Destination;
use Illuminate\Support\Facades\Http;

$cmd = $argv[1] ?? '';
$refs = array_filter(explode(',', $argv[2] ?? ''));
$apply = in_array('--apply', $argv, true);
$front = 'https://dev-app.norteia.app.br';
$total = 0;

foreach ($refs as $ref) {
    $isDestino = str_starts_with($ref, 'ibge:');
    $model = ($isDestino ? Destination::class : Attraction::class)::withTrashed()->where('source_ref', $ref)->first()?->canonical();
    if (! $model) {
        echo "$ref\tnão existe na API\n";
        continue;
    }
    $media = $model->media()->get()->filter(fn ($m) => $m->getCustomProperty('origin') === 'brave');
    $total += $media->count();

    if ($cmd === 'cleanup') {
        if ($apply) {
            $media->each->delete(); // Spatie removes the original + conversions from the disk
        }
        echo "$ref\t{$media->count()} foto(s) do Brave".($apply ? ' apagadas' : '')."\n";
        continue;
    }

    $path = ($isDestino ? 'destinations/' : 'attractions/').$model->slug;
    $res = Http::acceptJson()->timeout(20)->get(config('app.url').'/api/v1/'.$path);
    $data = $res->json('data') ?? $res->json() ?? [];
    $photos = $media->map(function ($m) {
        $done = collect($m->getGeneratedConversions())->filter()->keys()->implode(',');
        return "{$m->collection_name}#{$m->order_column} [{$m->disk}] conv=".($done ?: '-');
    })->implode('; ');
    printf("%s\t%s\tapi=%d cover_image=%s images=%d\t%s\t%s/%s\n",
        $ref, $model->slug, $res->status(), empty($data['cover_image']) ? 'não' : 'sim',
        count($data['images'] ?? []), $photos ?: 'sem foto', $front, $path);
}

echo "total: $total foto(s) origin=brave".($cmd === 'cleanup' && ! $apply ? ' (dry-run, use --apply)' : '')."\n";
