import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

type BadgeVariant='default'|'secondary'|'destructive'|'outline'
const variants:Record<BadgeVariant,string>={
 default:'border-transparent bg-primary text-primary-foreground',
 secondary:'border-transparent bg-secondary text-secondary-foreground',
 destructive:'border-transparent bg-destructive text-white',
 outline:'bg-background text-foreground',
}

export const Badge=({className,variant='default',...props}:HTMLAttributes<HTMLDivElement>&{variant?:BadgeVariant})=><div data-slot="badge" className={cn('inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold',variants[variant],className)} {...props}/>
