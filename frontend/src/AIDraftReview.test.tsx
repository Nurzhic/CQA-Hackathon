import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {expect,test} from 'vitest';
import {AIDraftReview} from './AIDraftReview';
test('review is visibly separate and starts without importing any labels',()=>{
 const html=renderToStaticMarkup(<AIDraftReview onBack={()=>{}}/>);
 expect(html).toContain('Загрузить AI-черновик');expect(html).toContain('type="file"');expect(html).toContain('не создаёт независимые A/B или consensus');expect(html).not.toContain('Сохранить человеческую версию');
});
