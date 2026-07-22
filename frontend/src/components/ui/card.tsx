import * as React from 'react'; import { cn } from '@/lib/utils'
export const Card=({className,...p}:React.HTMLAttributes<HTMLDivElement>)=><div data-slot="card" className={cn('rounded-xl border bg-card text-card-foreground shadow-sm',className)} {...p}/>
export const CardHeader=({className,...p}:React.HTMLAttributes<HTMLDivElement>)=><div data-slot="card-header" className={cn('grid gap-1.5 p-5',className)} {...p}/>
export const CardTitle=({className,...p}:React.HTMLAttributes<HTMLHeadingElement>)=><h3 data-slot="card-title" className={cn('font-semibold tracking-tight',className)} {...p}/>
export const CardDescription=({className,...p}:React.HTMLAttributes<HTMLParagraphElement>)=><p data-slot="card-description" className={cn('text-sm text-muted-foreground',className)} {...p}/>
export const CardContent=({className,...p}:React.HTMLAttributes<HTMLDivElement>)=><div data-slot="card-content" className={cn('px-5 pb-5',className)} {...p}/>
export const CardFooter=({className,...p}:React.HTMLAttributes<HTMLDivElement>)=><div data-slot="card-footer" className={cn('flex items-center gap-2 border-t bg-muted/30 px-5 py-3',className)} {...p}/>
