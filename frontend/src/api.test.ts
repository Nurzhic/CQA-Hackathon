import {describe,it,expect} from 'vitest';
import {displayValue,filtered} from './api';
describe('conversation presentation',()=>{
 it('distinguishes N/A, false and zero',()=>{expect(displayValue(null)).toBe('N/A');expect(displayValue(false)).toBe('Нет');expect(displayValue(0)).toBe('0')});
 it('combines search and language/dataset filters',()=>{const rows=[{conversation_id:'p1-ru-001',language:'RU',channel:'agent chat',dataset:'DEV'},{conversation_id:'p1-kz-001',language:'KZ',channel:'voice call',dataset:'DEV'}];expect(filtered(rows,'001','KZ','voice call','DEV')).toEqual([rows[1]]);expect(filtered(rows,'p1','EN','','')).toEqual([])});
});
