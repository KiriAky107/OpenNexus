// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createMediaSubmission, mediaService, type MediaJob } from './mediaService'

afterEach(() => vi.restoreAllMocks())

it('reuses upload and job identities after lost responses, until explicitly reset', async () => {
  const upload = vi.spyOn(mediaService, 'upload').mockRejectedValueOnce(new Error('response lost'))
    .mockResolvedValue({attachment_id:'uploaded'})
  const create = vi.spyOn(mediaService, 'create').mockRejectedValueOnce(new Error('response lost'))
    .mockResolvedValue({job_id:'same-job'} as MediaJob)
  const submission = createMediaSubmission()
  const file = new File(['audio'], 'lecture.wav')
  const options = {local_only:true}
  await expect(submission.submit(file, options)).rejects.toThrow('response lost')
  await expect(submission.submit(file, options)).rejects.toThrow('response lost')
  expect(await submission.submit(file, options)).toEqual({job_id:'same-job'})
  expect(upload).toHaveBeenCalledTimes(2)
  expect(upload.mock.calls[0][1]).toBe(upload.mock.calls[1][1])
  expect(create.mock.calls[0][0]).toEqual(create.mock.calls[1][0])
  submission.reset()
  await submission.submit(file, options)
  expect(upload.mock.calls[2][1]).not.toBe(upload.mock.calls[1][1])
  expect(create.mock.calls[2][0]).not.toEqual(create.mock.calls[1][0])
})

it('freezes options across upload and treats changed options as a new request', async () => {
  let release!: (value:{attachment_id:string}) => void
  vi.spyOn(mediaService, 'upload').mockImplementationOnce(() => new Promise(resolve => { release = resolve }))
    .mockResolvedValue({attachment_id:'next'})
  const create = vi.spyOn(mediaService, 'create').mockResolvedValue({job_id:'job'} as MediaJob)
  const submission = createMediaSubmission()
  const file = new File(['audio'], 'lecture.wav')
  const options = {local_only:true}
  const pending = submission.submit(file, options)
  options.local_only = false
  release({attachment_id:'first'})
  await pending
  expect(create.mock.calls[0][0]).toMatchObject({local_only:true})
  await submission.submit(file, options)
  expect(create.mock.calls[1][0]).toMatchObject({local_only:false})
})
